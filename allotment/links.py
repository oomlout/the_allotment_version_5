"""Public link metadata. No browser sessions, credentials, scripts or remote embeds."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit, urljoin, quote

from bs4 import BeautifulSoup
from .storage import now

MAX_LINKS=12
MAX_BYTES=1_000_000


def clean_url(value):
    value=str(value).strip()
    if not value:return ''
    if len(value)>2048 or re.search(r'[\x00-\x20\x7f\\]',value):raise ValueError('Use a complete http:// or https:// link without spaces.')
    try:
        parts=urlsplit(value)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('Use a public http:// or https:// link without credentials.')
        if parts.port not in (None,80,443):raise ValueError('Links must use standard HTTP or HTTPS ports.')
        host=parts.hostname.encode('idna').decode('ascii').lower()
        netloc=f'[{host}]' if ':' in host else host
        if parts.port:netloc+=f':{parts.port}'
        return urlunsplit((parts.scheme,netloc,quote(parts.path,safe='/%:@!$&\'()*+,;=-._~'),quote(parts.query,safe='=&%/:?@!$\'()*+,;~-._'),parts.fragment))
    except (UnicodeError,TypeError) as error:raise ValueError('Invalid link.') from error


def github_url(value):
    value=clean_url(value)
    if not value:return ''
    parts=urlsplit(value);path=parts.path.strip('/').removesuffix('.git')
    if parts.hostname!='github.com' or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',path):
        raise ValueError('Use a GitHub repository URL: https://github.com/owner/repository')
    return 'https://github.com/'+path


def public_address(host,port):
    addresses=list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('Metadata is available only for public websites.')
    return addresses[0]


def download(url):
    """Pin each connection to its checked public IP, including every redirect."""
    for _ in range(4):
        url=clean_url(url);parts=urlsplit(url);port=parts.port or (443 if parts.scheme=='https' else 80)
        address=public_address(parts.hostname,port)
        connection=http.client.HTTPConnection(parts.hostname,port,timeout=5)
        sock=None
        try:
            sock=socket.create_connection((address,port),timeout=5)
            if parts.scheme=='https':sock=ssl.create_default_context().wrap_socket(sock,server_hostname=parts.hostname)
            connection.sock=sock
            connection.request('GET',urlunsplit(('', '',parts.path or '/',parts.query,'')),headers={
                'Host':parts.netloc,'User-Agent':'TheAllotment/1.0 (link preview)','Accept':'text/html,application/xhtml+xml','Accept-Encoding':'identity'})
            response=connection.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location:raise ValueError('Redirect has no destination.')
                url=urljoin(url,location);continue
            if response.status!=200:raise ValueError(f'Website returned HTTP {response.status}.')
            if response.getheader('Content-Type','').split(';')[0].lower() not in ('text/html','application/xhtml+xml'):
                raise ValueError('This link does not return a web page.')
            if response.getheader('Content-Encoding','identity').lower() not in ('','identity'):
                raise ValueError('Website returned unsupported encoding.')
            body=response.read(MAX_BYTES+1)
            if len(body)>MAX_BYTES:body=body[:MAX_BYTES]
            return body,url
        finally:
            connection.close()
            if sock:sock.close()
    raise ValueError('Too many redirects.')


def metadata(url,kind='link'):
    result={'url':url,'kind':kind,'title':'','description':'','site_name':urlsplit(url).hostname,'fetched_at':now()}
    try:
        body,final=download(url)
        soup=BeautifulSoup(body,'html.parser')
        def meta(*names):
            for name in names:
                node=soup.find('meta',attrs={'property':name}) or soup.find('meta',attrs={'name':name})
                if node and node.get('content'):return node['content']
            return ''
        def text(value,limit):return ' '.join(str(value).split())[:limit]
        result.update(title=text(meta('og:title','twitter:title') or (soup.title.get_text() if soup.title else ''),300),
            description=text(meta('og:description','description','twitter:description'),600),
            site_name=text(meta('og:site_name') or urlsplit(final).hostname,100),resolved_url=final,status='ready')
    except (ValueError,OSError,http.client.HTTPException) as error:
        result.update(status='unavailable',error=str(error)[:180])
    return result


def prepare_links(repo_url,urls,previous=(),fetch=True):
    repo_url=github_url(repo_url)
    values=list(dict.fromkeys(url for value in urls if (url:=clean_url(value))))
    if repo_url:values=[repo_url,*[url for url in values if url!=repo_url]]
    if len(values)>MAX_LINKS:raise ValueError(f'Use up to {MAX_LINKS} links including the repository.')
    existing={entry['url']:entry for entry in previous}
    def prepare(url):
        kind='github' if url==repo_url else 'link';old=existing.get(url,{})
        if old.get('status')=='ready' or (not fetch and old):return {**old,'kind':kind}
        if fetch:return metadata(url,kind)
        return {'url':url,'kind':kind,'status':'pending','title':'','description':''}
    with ThreadPoolExecutor(max_workers=4) as pool:entries=list(pool.map(prepare,values))
    return repo_url,entries
