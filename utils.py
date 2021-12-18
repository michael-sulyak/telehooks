import json
import os
import ssl
import typing

import aiohttp
from aiogram import Bot


def get_config() -> dict:
    with open('./config.json') as file:
        return json.load(file)


def get_bots(config: dict) -> typing.Dict[str, Bot]:
    return {
        bot['slug']: Bot(token=bot['token'])
        for bot in config['bots']
    }


async def get_my_ip() -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.ipify.org') as response:
            response.raise_for_status()
            return (await response.read()).decode('utf-8')


async def get_ssl_context() -> ssl.SSLContext:
    ip = await get_my_ip()

    webhook_ssl_pem = './certificate/cert.pem'
    webhook_ssl_key = './certificate/private.key'

    os.system('mkdir certificate >/dev/null 2>&1')

    os.system(
        f'openssl req -newkey rsa:2048 -sha256 -nodes -keyout {webhook_ssl_key} -x509 -days 365 '
        f'-out {webhook_ssl_pem} -subj "/C=US/ST=New York/L=Brooklyn/O=Example Brooklyn Company/CN={ip}" '
        f'>/dev/null 2>&1'
    )

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(certfile=webhook_ssl_pem, keyfile=webhook_ssl_key)

    return ssl_context
