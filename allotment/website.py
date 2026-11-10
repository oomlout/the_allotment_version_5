from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .storage import inside, slug
from .images import media_files, display_file
from .tags import catalogue


def build_site(store, namespace, user, overrides=None):
    """Emit a portable, relative-URL site. No Flask or JS is needed to host it."""
    repo = store.repo(namespace, user)
    profile = store.profile(namespace, user)
    posts = store.posts(namespace, user)
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
    env.filters["date"] = lambda value: value[:10] if value else ""
    env.globals['display_file']=display_file
    template = env.get_template("public.html")
    output = inside(store.output, f"{slug(namespace)}/{slug(user)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{user}-", dir=output.parent))
    try:
        (stage / "assets").mkdir()
        if (output/'summary.md').is_file():shutil.copy2(output/'summary.md',stage/'summary.md')
        shutil.copy2(Path(__file__).parent / "static" / "public.css", stage / "assets" / "style.css")
        (stage / "assets" / "custom.css").write_text(profile.get("theme", {}).get("custom_css", ""), encoding="utf-8")
        for field in ["avatar", "banner"]:
            if profile.get(field):
                src = inside(repo, profile[field])
                dst = inside(stage, profile[field])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for post in posts:
            override=(overrides or {}).get(post['id'])
            source_path=override['path'] if override else post['path']
            if override:post['attachments']=override['attachments']
            post["destinations"] = store.destination_states(namespace, user, post["id"])
            for media in post.get("attachments", []):
                for filename in media_files(media):
                    src = inside(repo, f"{source_path}/{filename}")
                    dst = inside(stage, f"{post['path']}/{filename}")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
        tag_list=catalogue(store,namespace,user)['tags']
        common = {"profile": profile, "total": len(posts),'catalogue':tag_list}
        (stage / "index.html").write_text(template.render(**common, posts=posts, prefix="", single=False), encoding="utf-8")
        for post in posts:
            folder = stage / "p" / post["id"]
            folder.mkdir(parents=True)
            (folder / "index.html").write_text(template.render(**common, posts=[post], prefix="../../", single=True), encoding="utf-8")
        for tag in tag_list:
            matching=[p for p in posts if tag['id'] in p.get('tags',[])]
            if not matching:continue
            folder=stage/'tags'/slug(tag['id']);folder.mkdir(parents=True)
            (folder/'index.html').write_text(template.render(**common,posts=matching,prefix='../../',single=True,tag=tag),encoding='utf-8')
        # Replace only this user's previously generated output after a complete build.
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return output
