import json


def load_config(file_path: str = './config.json') -> dict:
    with open(file_path) as file:
        return json.load(file)


RAW_CONFIG = load_config()
DEBUG = RAW_CONFIG['debug']
SENTRY_DSN = RAW_CONFIG['sentry_dsn']
BOTS_INFO = RAW_CONFIG['bots']
WEBHOOK_PORT = RAW_CONFIG['port']
DROP_PENDING_UPDATES = RAW_CONFIG['drop_pending_updates']
MAX_CONNECTIONS = RAW_CONFIG['max_connections']
AMQP_URL = RAW_CONFIG['amqp_url']
AMQP_MSG_EXPIRATION = RAW_CONFIG['amqp_msg_expiration']
PROXY = RAW_CONFIG.get('proxy')
SSL_CERT_PATH = './certificate/cert.pem'
SSL_KEY_PATH = './certificate/private.key'
