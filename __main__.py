import asyncio
import logging
import typing
import uuid
import itertools
from time import sleep

import aio_pika
import sentry_sdk
from aiogram import Bot
from aiohttp import web
from sentry_sdk.integrations.aiohttp import AioHttpIntegration

import utils


CONFIG = utils.get_config()

logging_level = logging.DEBUG if CONFIG['debug'] else logging.INFO
logging.basicConfig(level=logging_level)

sentry_sdk.init(
    dsn=CONFIG['sentry_dsn'],
    integrations=(AioHttpIntegration(),),
)

BOTS = utils.get_bots(CONFIG)
WEBHOOK_PORT = CONFIG['port']

LOOP = asyncio.new_event_loop()

AMQP_CONNECTION = None
for i in itertools.count():
    try:
        AMQP_CONNECTION = LOOP.run_until_complete(aio_pika.connect_robust(CONFIG['amqp_url'], loop=LOOP))
    except ConnectionError:
        if i == 60:
            raise

        logging.info('Waiting MQ...')
        sleep(1)
    else:
        break

AMQP_CHANNEL = LOOP.run_until_complete(AMQP_CONNECTION.channel())
AMQP_MSG_EXPIRATION = CONFIG['amqp_msg_expiration']


async def init_handlers(app: web.Application) -> None:
    ip = await utils.get_my_ip()
    webhook_ssl_pem = './certificate/cert.pem'

    def _create_on_startup(bot_: Bot, url: str) -> typing.Callable:
        async def _on_startup(app_: web.Application) -> None:
            logging.info('Listening %s...', url)
            with open(webhook_ssl_pem) as cert:
                await bot_.set_webhook(
                    url,
                    certificate=cert,
                    ip_address=ip,
                    drop_pending_updates=True,
                    max_connections=40,
                )

        return _on_startup

    def _create_on_shutdown(bot_: Bot) -> typing.Callable:
        async def _on_shutdown(app_: web.Application) -> None:
            await bot_.delete_webhook()

        return _on_shutdown

    def _create_handler(routing_key: str) -> typing.Callable:
        async def _handle(request: web.Request) -> web.Response:
            await AMQP_CHANNEL.default_exchange.publish(
                aio_pika.Message(await request.read(), expiration=AMQP_MSG_EXPIRATION),
                routing_key=routing_key,
            )
            return web.Response()

        return _handle

    for bot_slug, bot in BOTS.items():
        endpoint_for_webhook = str(uuid.uuid4())
        webhook_url = f'https://{ip}:{WEBHOOK_PORT}/{endpoint_for_webhook}/'

        await AMQP_CHANNEL.declare_queue(bot_slug, auto_delete=True)

        app.router.add_post(f'/{endpoint_for_webhook}/', _create_handler(bot_slug))
        app.on_startup.append(_create_on_startup(bot, webhook_url))
        app.on_shutdown.append(_create_on_shutdown(bot))


async def on_startup(app: web.Application) -> None:
    logging.info('Starting...')


async def on_shutdown(app: web.Application) -> None:
    await AMQP_CONNECTION.close()


def main() -> typing.NoReturn:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    LOOP.run_until_complete(init_handlers(app))

    web.run_app(
        app,
        host='0.0.0.0',
        port=WEBHOOK_PORT,
        ssl_context=LOOP.run_until_complete(utils.get_ssl_context()),
        loop=LOOP,
    )


main()
LOOP.close()
