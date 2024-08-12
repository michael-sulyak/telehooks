import utils


RAW_CONFIG = utils.load_config()
DEBUG = RAW_CONFIG['debug']
SENTRY_DSN = RAW_CONFIG['sentry_dsn']
BOTS = utils.get_bots(RAW_CONFIG['bots'])
WEBHOOK_PORT = RAW_CONFIG['port']
DROP_PENDING_UPDATES = RAW_CONFIG['drop_pending_updates']
MAX_CONNECTIONS = RAW_CONFIG['max_connections']
AMQP_URL = RAW_CONFIG['amqp_url']
AMQP_MSG_EXPIRATION = RAW_CONFIG['amqp_msg_expiration']
SSL_CERT_PATH = './certificate/cert.pem'
SSL_KEY_PATH = './certificate/private.key'
