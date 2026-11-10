"""Editorial framing defaults, separate from browser workflow approvals."""
from .storage import read_yaml,write_yaml,slug
from .images import RATIOS,LABELS

DEFAULTS={'bluesky':'landscape','twitter':'landscape','discord':'original','whatsapp':'original','whatsapp_channel':'portrait',
    'telegram':'original','telegram_channel':'original','instagram':'portrait','threads':'portrait','facebook':'landscape',
    'facebook_business':'landscape','tumblr':'portrait','mastodon':'original','medium':'landscape','pixelfed':'square',
    'flickr':'original','makerworld':'landscape','printables':'landscape','patreon':'landscape','static':'original','summary':'original','git':'original'}
CHOICES={'original':'Original',**LABELS}

def defaults(store,namespace,user):
    saved=read_yaml(store.local/'framing'/slug(namespace)/f'{slug(user)}.yaml')
    return {name:saved.get(name,DEFAULTS.get(name,'original')) for name in [*store.plugins(),'static','summary','git']}

def save_defaults(store,namespace,user,values):
    allowed=defaults(store,namespace,user)
    if set(values)-set(allowed) or any(v not in CHOICES for v in values.values()):raise ValueError('Choose a supported image framing.')
    write_yaml(store.local/'framing'/slug(namespace)/f'{slug(user)}.yaml',{**allowed,**values})

def overrides(values,allowed):
    result={}
    for key,value in values.items():
        if key not in allowed:raise ValueError('Unknown destination override.')
        if value and value!='default':
            if value not in CHOICES:raise ValueError('Choose a supported crop.')
            result[key]={'framing':value}
    return result
