import asyncio
import json
import logging
import os
import ssl
import subprocess
import typing

import aio_pika
import aiohttp
from aio_pika.abc import AbstractRobustConnection
from aiogram import Bot
from async_lru import alru_cache


def load_config(file_path: str = './config.json') -> dict:
    with open(file_path) as file:
        return json.load(file)


def get_bots(raw_bots: list[dict]) -> typing.Dict[str, Bot]:
    return {
        raw_bot['slug']: Bot(token=raw_bot['token'])
        for raw_bot in raw_bots
    }


@alru_cache(maxsize=1)
async def get_my_ip() -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.ipify.org') as response:
            response.raise_for_status()
            return (await response.read()).decode('utf-8')


def generate_ssl_certificate(*, ip: str, ssl_cert_path: str, ssl_key_path: str) -> None:
    cert_dir = os.path.dirname(ssl_cert_path)
    os.makedirs(cert_dir, exist_ok=True)

    cmd = [
        'openssl', 'req',
        '-newkey', 'rsa:2048',
        '-sha256', '-nodes',
        '-keyout', ssl_key_path,
        '-x509', '-days', '365',
        '-out', ssl_cert_path,
        '-subj', f'/C=US/ST=NY/L=Brooklyn/O=Telehooks/CN={ip}',
        '-addext', f'subjectAltName=IP:{ip}',
    ]

    subprocess.run(cmd, check=True)

    os.chmod(ssl_key_path, 0o600)


def get_ssl_context(*, ssl_cert_path: str, ssl_key_path: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=ssl_cert_path, keyfile=ssl_key_path)
    return ctx


async def connect_robust_to_mq(*args, **kwargs) -> AbstractRobustConnection:
    max_retries = 20

    for i in range(max_retries):
        try:
            return await aio_pika.connect_robust(*args, **kwargs)
        except (ConnectionError, aio_pika.exceptions.AMQPConnectionError) as e:
            if i + 1 == max_retries:
                logging.error('Failed to connect to AMQP after %d attempts: %s', max_retries, e)
                raise

            logging.info('Retrying AMQP connection (%d/%d)...', i + 1, max_retries)

            await asyncio.sleep(1)
