import asyncio
import json
import logging
import os
import ssl
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
    os.system('mkdir certificate >/dev/null 2>&1')

    os.system(
        f'openssl req -newkey rsa:2048 -sha256 -nodes -keyout {ssl_key_path} -x509 -days 365 '
        f'-out {ssl_cert_path} -subj "/C=US/ST=New York/L=Brooklyn/O=Example Brooklyn Company/CN={ip}" '
        f'>/dev/null 2>&1'
    )


def get_ssl_context(*, ssl_cert_path: str, ssl_key_path: str) -> ssl.SSLContext:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
    ssl_context.load_cert_chain(certfile=ssl_cert_path, keyfile=ssl_key_path)
    return ssl_context


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
