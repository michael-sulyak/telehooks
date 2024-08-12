import asyncio
import logging
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


async def init_handlers(app: web.Application) -> None:
    global amqp_connection

    logging.info('Initializing AMQP connection...')
    amqp_connection = await utils.connect_robust_to_mq(config.AMQP_URL, loop=loop, timeout=60)
    logging.info('AMQP connection established.')

    ip = await utils.get_my_ip()

    def _create_on_startup(bot_: Bot, url: str) -> typing.Callable:
        async def _on_startup(app_: web.Application) -> None:
            logging.info('Setting webhook...')

            await bot_.set_webhook(
                url=url,
                certificate=FSInputFile(path=config.SSL_CERT_PATH),
                ip_address=ip,
                drop_pending_updates=config.DROP_PENDING_UPDATES,
                max_connections=config.MAX_CONNECTIONS,
            )

            logging.info('Listening %s...', url)

        return _on_startup

    def _create_on_shutdown(bot_: Bot) -> typing.Callable:
        async def _on_shutdown(app_: web.Application) -> None:
            await bot_.delete_webhook()

        return _on_shutdown

    def _create_handler(routing_key: str) -> typing.Callable:
        async def _handle(request: web.Request) -> web.Response:
            amqp_channel = await amqp_connection.channel()

            try:
                await amqp_channel.default_exchange.publish(
                    aio_pika.Message(await request.read(), expiration=config.AMQP_MSG_EXPIRATION),
                    routing_key=routing_key,
                )
            finally:
                await amqp_channel.close()

            return web.Response()

        return _handle

    amqp_channel = await amqp_connection.channel()

    for bot_slug, bot in config.BOTS.items():
        endpoint_for_webhook = str(uuid.uuid4())
        webhook_url = f'https://{ip}:{config.WEBHOOK_PORT}/{endpoint_for_webhook}/'

        logging.info('Declaring queue "%s"...', bot_slug)
        await amqp_channel.declare_queue(bot_slug)

        logging.info('Creating handler for %s...', bot_slug)
        app.router.add_post(f'/{endpoint_for_webhook}/', _create_handler(bot_slug))
        app.on_startup.append(_create_on_startup(bot, webhook_url))
        app.on_shutdown.append(_create_on_shutdown(bot))

    await amqp_channel.close()


async def on_startup(app: web.Application) -> None:
    logging.info('Starting...')


async def on_shutdown(app: web.Application) -> None:
    logging.info('Stopping...')

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
        port=config.WEBHOOK_PORT,
        ssl_context=utils.get_ssl_context(
            ssl_key_path=config.SSL_KEY_PATH,
            ssl_cert_path=config.SSL_CERT_PATH,
        ),
        loop=loop,
    )


if __name__ == '__main__':
    main()
