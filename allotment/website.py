from __future__ import annotations

from pathlib import Path
from datetime import datetime
from copy import deepcopy
import os
import re
import shutil
import tempfile
from urllib.parse import urlparse
import zipfile

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .storage import inside, slug
from .images import display_file
from .tags import catalogue


def public_url(value):
    """Only clickable public web addresses belong in a static export."""
    return isinstance(value, str) and urlparse(value).scheme in {'http', 'https'} and bool(urlparse(value).netloc)


def follow_links(store, namespace, user, profile):
    accounts = store.settings(namespace, user).get('destinations', {})
    social = dict(profile.get('social', {}))
    # An explicit public-link preference wins over an older profile snapshot.
    # Never export browser URLs, channel targets, credentials or account evidence.
    for platform, account in accounts.items():
        if account.get('publish_profile_link') is False:
            social.pop(platform, None)
        elif account.get('publish_profile_link') and public_url(account.get('profile_url')):
            social[platform] = account['profile_url']
    logos = Path(__file__).parent / 'static' / 'platform-logos'
    links, seen = [], set()
    for platform, address in social.items():
        if not public_url(address) or address.rstrip('/') in seen:
            continue
        seen.add(address.rstrip('/'))
        logo = logos / f'{platform}.svg' if re.fullmatch(r'[a-z0-9_]+', platform) else None
        links.append({'id': platform, 'name': profile.get('social_names', {}).get(platform,
                      platform.replace('_', ' ').title()), 'url': address,
                      'address': re.sub(r'^https?://(?:www\.)?', '', address).rstrip('/'),
                      'logo': logo.name if logo and logo.is_file() else None})
    return sorted(links, key=lambda link: link['name'].casefold())


def date_label(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%d %b %Y').lstrip('0')
    except (ValueError, AttributeError):
        return str(value or '')[:10]


def theme_css(theme):
    # Put theme values in a stylesheet, rather than inline styles, so custom.css
    # can override every token. Accept only validated scalar CSS values.
    values = []
    for field, token in [('accent', 'accent'), ('background', 'background')]:
        if re.fullmatch(r'#[0-9a-fA-F]{6}', str(theme.get(field, ''))):
            values.append(f'--{token}: {theme[field]};')
    width = theme.get('width', 1120)
    if isinstance(width, int) and 480 <= width <= 1400:
        values.append(f'--feed-width: {width}px;')
    return ':root { ' + ' '.join(values) + ' }\n'


def package_site(store, output):
    """Export the latest selected garden, with index.html at the ZIP root."""
    from .storage import lock
    export = store.root / 'website_static'
    export.mkdir(exist_ok=True)
    with lock(export / '.build.lock'):
        stage = Path(tempfile.mkdtemp(prefix='.export-', dir=export))
        archive = stage / 'the_allotment.zip'
        destination = inside(export, 'dist')
        try:
            shutil.copytree(output, stage / 'dist')
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
                for file in sorted((stage / 'dist').rglob('*')):
                    if file.is_file():
                        bundle.write(file, file.relative_to(stage / 'dist').as_posix())
            if destination.exists():
                shutil.rmtree(destination)
            (stage / 'dist').rename(destination)
            os.replace(archive, export / 'the_allotment.zip')
        finally:
            shutil.rmtree(stage)
    return export / 'the_allotment.zip'


def build_site(store, namespace, user, overrides=None):
    """Emit a portable, relative-URL site. No Flask or JS is needed to host it."""
    repo = store.repo(namespace, user)
    profile = deepcopy(store.profile(namespace, user))
    posts = deepcopy(store.posts(namespace, user))
    profile['website'] = profile.get('website') if public_url(profile.get('website')) else ''
    # A later site rebuild preserves each post's last successfully sown crop.
    from .lifecycle import revision
    saved_overrides={}
    current={post['id']:post for post in posts}
    for job in store.jobs(namespace,user):
        payload=job.get('destination_posts',{}).get('static')
        post=current.get(job['post']['id'])
        if payload and post and post['id'] not in saved_overrides and job['actions'].get('static',{}).get('status')=='succeeded' and job['revision']==revision(post):
            saved_overrides[post['id']]=payload
    overrides={**saved_overrides,**(overrides or {})}
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.filters["date"] = date_label
    env.globals['display_file']=display_file
    template = env.get_template("public.html")
    output = inside(store.output, f"{slug(namespace)}/{slug(user)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{user}-", dir=output.parent))
    try:
        (stage / "assets").mkdir()
        if (output / 'summary.md').is_file():
            shutil.copy2(output / 'summary.md', stage / 'summary.md')
        shutil.copy2(Path(__file__).parent / "static" / "public.css", stage / "assets" / "style.css")
        (stage / 'assets' / 'theme.css').write_text(theme_css(profile.get('theme', {})), encoding='utf-8')
        (stage / "assets" / "custom.css").write_text(profile.get("theme", {}).get("custom_css", ""), encoding="utf-8")
        links = follow_links(store, namespace, user, profile)
        logo_source = Path(__file__).parent / 'static' / 'platform-logos'
        (stage / 'assets' / 'logos').mkdir()
        for name in {link['logo'] for link in links if link['logo']} | {'sources.json', 'simple-LICENSE.txt', 'dashboard-LICENSE.txt'}:
            shutil.copy2(logo_source / name, stage / 'assets' / 'logos' / name)
        for field in ["avatar", "banner"]:
            if profile.get(field):
                src = inside(repo, profile[field])
                dst = inside(stage, profile[field])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for post in posts:
            slug(post['id'])
            override=(overrides or {}).get(post['id'])
            source_path=override['path'] if override else post['path']
            if override:post['attachments']=deepcopy(override['attachments'])
            post["destinations"] = store.destination_states(namespace, user, post["id"])
            post['links'] = [link for link in post.get('links', []) if public_url(link.get('url'))]
            post['destinations'] = {name: receipt for name, receipt in post['destinations'].items()
                                    if public_url(receipt.get('url')) and not receipt.get('private_link')}
            for media in post.get("attachments", []):
                # Only the chosen presentation image is needed by the website.
                for filename in [display_file(media)]:
                    src = inside(repo, f"{source_path}/{filename}")
                    dst = inside(stage, f"{post['path']}/{filename}")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                # Local responsive previews keep the feed light; original media
                # stays available on the detail page. Never alter garden assets.
                if media.get('type') == 'image':
                    from PIL import Image, ImageOps, UnidentifiedImageError
                    try:
                        with Image.open(src) as image:
                            if not getattr(image, 'is_animated', False):
                                preview = ImageOps.exif_transpose(image).convert('RGB')
                                preview.thumbnail((960, 960))
                                thumb = dst.with_name(dst.name + '.preview.webp')
                                preview.save(thumb, 'WEBP', quality=85)
                                media['preview'] = thumb.relative_to(stage).as_posix()
                    except (UnidentifiedImageError, OSError):
                        pass  # Preserve supported original media even without a preview.
        tag_list=catalogue(store,namespace,user)['tags']
        common = {"profile": profile, "total": len(posts),'catalogue':tag_list, 'follow_links': links}
        (stage / "index.html").write_text(template.render(**common, posts=posts, prefix="", single=False), encoding="utf-8")
        (stage / 'follow').mkdir()
        (stage / 'follow' / 'index.html').write_text(env.get_template('public_follow.html').render(
            **common, prefix='../', follow=True), encoding='utf-8')
        for index, post in enumerate(posts):
            folder = stage / "p" / post["id"]
            folder.mkdir(parents=True)
            (folder / "index.html").write_text(template.render(**common, posts=[post], prefix="../../", single=True,
                newer=posts[index-1] if index else None, older=posts[index+1] if index+1<len(posts) else None), encoding="utf-8")
        for tag in tag_list:
            matching=[p for p in posts if tag['id'] in p.get('tags',[])]
            if not matching:continue
            folder=stage/'tags'/slug(tag['id']);folder.mkdir(parents=True)
            (folder/'index.html').write_text(template.render(**common,posts=matching,prefix='../../',single=False,tag=tag),encoding='utf-8')
        # Replace only this user's previously generated output after a complete build.
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    package_site(store, output)
    return output
