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
amqp_connection = None
amqp_channel = None

channel_lock = asyncio.Lock()


async def ensure_channel() -> None:
    global amqp_connection, amqp_channel

    async with channel_lock:
        if amqp_channel is None or amqp_channel.is_closed:
            amqp_channel = await amqp_connection.channel()


async def init_handlers(app: web.Application) -> None:
    global amqp_connection, amqp_channel

    # Pre-generate one stable token per bot (in-memory for this process)
    secret_tokens_map: dict[str, str] = {
        slug: secrets.token_urlsafe(32)
        for slug in config.BOTS.keys()
    }

    logging.info('Initializing AMQP connection...')
    amqp_connection = await utils.connect_robust_to_mq(config.AMQP_URL, timeout=60)
    logging.info('AMQP connection established.')

    ip = await utils.get_my_ip()
    amqp_channel = await amqp_connection.channel()

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
            await bot_.delete_webhook()

        return _on_shutdown

    def _create_handler(slug: str) -> typing.Callable:
        async def _handle(request: web.Request) -> web.Response:
            provided = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
            expected = secret_tokens_map.get(slug)
            if not expected or not hmac.compare_digest(provided, expected):
                return web.Response(status=401)

            try:
                body = await request.read()
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
            except Exception:
                logging.exception('Publish failed')
                return web.Response(status=500)

            return web.Response(status=200)

        return _handle

    for bot_slug, bot in config.BOTS.items():
        endpoint_for_webhook = str(uuid.uuid4())
        webhook_url = f'https://{ip}:{config.WEBHOOK_PORT}/{endpoint_for_webhook}/'

        logging.info('Declaring queue "%s"...', bot_slug)
        await amqp_channel.declare_queue(bot_slug)

        logging.info('Creating handler for %s...', bot_slug)
        app.router.add_post(f'/{endpoint_for_webhook}/', _create_handler(bot_slug))
        app.on_startup.append(_create_on_startup(bot, bot_slug, webhook_url))
        app.on_shutdown.append(_create_on_shutdown(bot))


async def on_startup(app: web.Application) -> None:
    logging.info('Starting...')


async def on_shutdown(app: web.Application) -> None:
    logging.info('Stopping...')

    if amqp_channel is not None:
        await amqp_channel.close()

    if amqp_connection is not None:
        await amqp_connection.close()


def main() -> typing.NoReturn:
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

    loop.run_until_complete(init_handlers(app))

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
