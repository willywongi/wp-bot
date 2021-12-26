import json
import logging
import logging.config
import os
import time
from pathlib import Path
from socket import timeout
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from rest_tools.wordpress import get_wordpress_client

TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
BOT_STATUS = {
    'ASKED_APIKEY': 1,
    'ASKED_DATE': 2,
    'ASKED_SECRET': 3
}



def invoke(method, **kwargs):
    '''Invoke a method on the Telegram API.
        Passes any keyword argument as paramters of the Telegram API.
        eg.:
        >>> call_telegram("getMe")
        {u'ok': True, u'result': {u'username': u'samplebot', u'first_name': u'SampleBot', u'id': 123456789}}
    '''
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    if kwargs:
        data = bytes(urlencode(kwargs), "utf8")
    else:
        data = None

    try:
        handler = urlopen(url, data=data, timeout=60)
    except HTTPError as exc:
        print(f"Telegram HTTPError {exc.getcode()}, {exc.read()}")
        raise
    else:
        return json.loads(handler.read())

def callback(update, logger):
    message = update['message']
    # get user
    user = message['from']
    context_file_path = Path(".", "context", f"{user['id']}.json")
    if context_file_path.exists():
        # load user context
        with open(context_file_path, 'r') as handler:
            context = json.load(handler)
    else:
        context = {}
    
    credentials = context.get('credentials')
    document = message.get('document')
    login_command = next((ent for ent in message.get('entities', []) if ent['type'] == 'bot_command'), None)
    if login_command:
        context['credentials'] = message['text'][login_command['offset'] + login_command['length']:].strip()
        invoke("sendMessage", chat_id=message['chat']['id'], text="Ok, credenziali salvate.")
    elif not credentials:
        invoke("sendMessage", chat_id=message['chat']['id'], text="Prima di procedere, mandami le credenziali con il comando /login")
    elif document:
        remote_file_path = document.get('file_path')
        if not remote_file_path:
            response = invoke("getFile", file_id=document['file_id'])
            document.update(response['result'])
            
        remote_file_path = document['file_path']
        file_path = Path(".", "media", f"{document['file_unique_id']}__{document.get('file_name')}.mp3")
        with open(file_path, "wb") as write_handler:
            with urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{remote_file_path}") as remote_handler:
                write_handler.write(remote_handler.read())
        logger.info(f"Saved {file_path} from {remote_file_path}")

        invoke("sendMessage", chat_id=message['chat']['id'], text="Documento salvato. Di quando è la registrazione?")
        context.update({
            'file_path': str(file_path),
            'status': BOT_STATUS['ASKED_DATE']
        })
    
    elif context.get('status') == BOT_STATUS['ASKED_DATE']:
        apikey, secret = context['credentials'].split(":")
        wp_client = get_wordpress_client(apikey, secret, "https://www.sangiuseppesanbiagio.it/wp-json")
        context['date'] = message['text']
        invoke("sendMessage", chat_id=message['chat']['id'], text="Ok, sto per pubblicare l'audio")
        title = f"Vangelo e Omelia {context['date']}"
        media = wp_client("post", 
                            path="/wp/v2/media", 
                            data={'title': title},
                            file_object=open(context['file_path'], 'rb'))
        logger.info("Loaded media to %s", media.get('link'))
        post_args = {
            "status": "publish",
            "title": title,
            "content": media['description']['rendered'],
            "categories": "16",  # oppure "Audio Omelia"
        }
        post = wp_client("post", "/wp/v2/posts", data=post_args)
        logger.info("Created post %s", post.get('link'))
        invoke("sendMessage", chat_id=message['chat']['id'], text=f"Pubblicato qui: {post['link']}")
        context['status'] = BOT_STATUS['ASKED_APIKEY']
        del context['file_path']
        del context['date']

    # save user context
    with open(context_file_path, 'w+') as handler:
        json.dump(context, handler)


def bot(logger):
    update_id = None
    while True:
        try:
            data = invoke("getUpdates", offset=update_id)
        except timeout:
            continue
        else:
            updates = data.get('result', ())
            if updates:
                # offset: max(update_id) + 1
                update_id = max(u['update_id'] for u in updates) + 1

            if data['ok']:
                for update in updates:
                    callback(update, logger)
            else:
                logger.error("Bad response from Telegram: %s", data)
        finally:
            # give the server a little breath.
            time.sleep(0.5)

if __name__ == "__main__":
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        "formatters": {
            "standard": {
                "format": '%(asctime)s %(name)s %(levelname)s %(message)s'
            },
        },
        'handlers': {
            'bot': {
                'level': 'INFO',
                'filename': Path(".") / 'log' / 'bot.log',
                'formatter': 'standard',
                "class": "logging.handlers.RotatingFileHandler",
                "maxBytes": 1048576,  # 1MB
                "backupCount": 5
            },
        },
        'loggers': {
            'bot': {
                'handlers': ['bot'],
                'level': 'INFO',
            },
        },
    })

    bot(logger=logging.getLogger())
