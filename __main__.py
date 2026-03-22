import asyncio
import hmac
import logging
import secrets
import typing
import uuid

import aio_pika
import sentry_sdk
from aiogram import Bot
from aiogram.types import FSInputFile
from aiohttp import web
from sentry_sdk.integrations.aiohttp import AioHttpIntegration

import config
import utils


logging_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(level=logging_level)

sentry_sdk.init(
    dsn=config.SENTRY_DSN,
    integrations=(AioHttpIntegration(),),
)

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

amqp_connection = None
amqp_channel = None
bots: dict[str, Bot] = {}
poller_tasks: dict[str, asyncio.Task] = {}

channel_lock = asyncio.Lock()


async def ensure_channel() -> None:
    global amqp_connection, amqp_channel

    async with channel_lock:
        if amqp_channel is None or amqp_channel.is_closed:
            amqp_channel = await amqp_connection.channel()


async def publish_update(*, slug: str, body: bytes) -> None:
    message = aio_pika.Message(
        body,
        content_type='application/json',
        delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
        expiration=config.AMQP_MSG_EXPIRATION,
    )
    await ensure_channel()
    await amqp_channel.default_exchange.publish(
        message,
        routing_key=slug,
        mandatory=True,
    )


def serialize_update(update: typing.Any) -> bytes:
    if hasattr(update, 'model_dump_json'):
        return update.model_dump_json(
            by_alias=True,
            exclude_none=True,
        ).encode('utf-8')

    return update.json(
        by_alias=True,
        exclude_none=True,
    ).encode('utf-8')


async def init_amqp() -> None:
    global amqp_connection, amqp_channel

    logging.info('Initializing AMQP connection...')
    amqp_connection = await utils.connect_robust_to_mq(config.AMQP_URL, timeout=60)
    logging.info('AMQP connection established.')

    amqp_channel = await amqp_connection.channel()

    for bot_slug in bots.keys():
        logging.info('Declaring queue "%s"...', bot_slug)
        await amqp_channel.declare_queue(bot_slug)


async def shutdown_amqp() -> None:
    global amqp_connection, amqp_channel

    if amqp_channel is not None and not amqp_channel.is_closed:
        await amqp_channel.close()
        amqp_channel = None

    if amqp_connection is not None and not amqp_connection.is_closed:
        await amqp_connection.close()
        amqp_connection = None


async def init_webhook_handlers(app: web.Application) -> None:
    # Pre-generate one stable token per bot (in-memory for this process)
    secret_tokens_map: dict[str, str] = {
        slug: secrets.token_urlsafe(32)
        for slug in bots.keys()
    }

    await init_amqp()
    ip = await utils.get_my_ip()

    def _create_on_startup(bot_: Bot, slug: str, url: str) -> typing.Callable:
        async def _on_startup(app_: web.Application) -> None:
            logging.info('Setting webhook for %s...', slug)
            await bot_.set_webhook(
                url=url,
                certificate=FSInputFile(path=config.SSL_CERT_PATH),
                ip_address=ip,
                secret_token=secret_tokens_map[slug],
                drop_pending_updates=config.DROP_PENDING_UPDATES,
                max_connections=config.MAX_CONNECTIONS,
            )
            logging.info('Listening %s for %s', url, slug)

        return _on_startup

    def _create_on_shutdown(bot_: Bot) -> typing.Callable:
        async def _on_shutdown(app_: web.Application) -> None:
            try:
                await bot_.delete_webhook()
            finally:
                await bot_.session.close()

        return _on_shutdown

    def _create_handler(slug: str) -> typing.Callable:
        async def _handle(request: web.Request) -> web.Response:
            provided = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
            expected = secret_tokens_map.get(slug)
            if not expected or not hmac.compare_digest(provided, expected):
                return web.Response(status=401)

            try:
                body = await request.read()
                await publish_update(slug=slug, body=body)
            except Exception:
                logging.exception('Publish failed')
                return web.Response(status=500)

            return web.Response(status=200)

        return _handle

    for bot_slug, bot in bots.items():
        endpoint_for_webhook = str(uuid.uuid4())
        webhook_url = f'https://{ip}:{config.WEBHOOK_PORT}/{endpoint_for_webhook}/'

        logging.info('Creating handler for %s...', bot_slug)
        app.router.add_post(f'/{endpoint_for_webhook}/', _create_handler(bot_slug))
        app.on_startup.append(_create_on_startup(bot, bot_slug, webhook_url))
        app.on_shutdown.append(_create_on_shutdown(bot))


async def poll_bot_updates(bot: Bot, slug: str) -> None:
    logging.info('Starting pull strategy for %s...', slug)

    offset = None

    try:
        while True:
            try:
                await bot.delete_webhook(
                    drop_pending_updates=config.DROP_PENDING_UPDATES,
                )
                break
            except Exception:
                logging.exception('Failed to disable webhook for %s', slug)
                await asyncio.sleep(config.PULL_INTERVAL)

        while True:
            try:
                while True:
                    updates = await bot.get_updates(
                        offset=offset,
                        limit=100,
                        timeout=0,
                    )

                    if not updates:
                        break

                    for update in updates:
                        await publish_update(slug=slug, body=serialize_update(update))
                        offset = update.update_id + 1
            except Exception:
                logging.exception('Pull strategy failed for %s', slug)

            await asyncio.sleep(config.PULL_INTERVAL)
    except asyncio.CancelledError:
        logging.info('Pull strategy stopped for %s', slug)
        raise


async def start_pull_mode() -> None:
    await init_amqp()

    for bot_slug, bot in bots.items():
        poller_tasks[bot_slug] = asyncio.create_task(
            poll_bot_updates(bot, bot_slug),
        )


async def stop_pull_mode() -> None:
    logging.info('Stopping pull strategy...')

    tasks = list(poller_tasks.values())
    for task in tasks:
        task.cancel()

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    poller_tasks.clear()

    for bot_slug, bot in bots.items():
        try:
            await bot.session.close()
        except Exception:
            logging.exception('Failed to close bot session for %s', bot_slug)

    await shutdown_amqp()


async def on_startup(app: web.Application) -> None:
    logging.info('Starting...')


async def on_shutdown(app: web.Application) -> None:
    logging.info('Stopping...')
    await shutdown_amqp()


def main() -> None:
    global bots
    bots = utils.get_bots(config.BOTS_INFO)

    if config.USE_PULL_STRATEGY:
        logging.info(
            'Pull strategy is enabled. Poll interval: %s second(s)',
            config.PULL_INTERVAL,
        )

        try:
            loop.run_until_complete(start_pull_mode())
            loop.run_forever()
        except KeyboardInterrupt:
            logging.info('Stopping by keyboard interrupt...')
        finally:
            loop.run_until_complete(stop_pull_mode())
            loop.close()

        return

    logging.info('Getting the current IP... ')
    ip = loop.run_until_complete(utils.get_my_ip())
    logging.info('Current IP: %s', ip)

    logging.info('Generating SSL certificate...')
    utils.generate_ssl_certificate(
        ip=ip,
        ssl_key_path=config.SSL_KEY_PATH,
        ssl_cert_path=config.SSL_CERT_PATH,
    )
    logging.info('SSL certificate has been generated')

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    loop.run_until_complete(init_webhook_handlers(app))

    web.run_app(
        app,
        host='0.0.0.0',
        port=int(config.WEBHOOK_PORT),
        ssl_context=utils.get_ssl_context(
            ssl_key_path=config.SSL_KEY_PATH,
            ssl_cert_path=config.SSL_CERT_PATH,
        ),
        loop=loop,
    )


if __name__ == '__main__':
    main()
