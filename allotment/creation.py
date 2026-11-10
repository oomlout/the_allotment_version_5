"""Post organisation and image editing routes, independent of publishing workers."""
import hashlib
from io import BytesIO
import json

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for

from . import images, tags
from .storage import inside


def attachment_revision(attachment):
    return hashlib.sha256(json.dumps(attachment,sort_keys=True).encode()).hexdigest()


def register_creation(app, store, context, asset_url):
    bp=Blueprint('creation',__name__)

    def draft_image(namespace,user,post_id,index):
        folder=store.draft_dir(namespace,user,post_id)
        if not (folder/'post.yaml').exists():raise ValueError('Save this post as a draft before editing its images.')
        post=store.draft(namespace,user,post_id)
        return folder,post,images.checked_attachment(post,index)

    @bp.get('/g/<namespace>/<user>/images/<post_id>/<int:index>/')
    def image_editor(namespace,user,post_id,index):
        folder,post,attachment=draft_image(namespace,user,post_id,index)
        path=inside(folder,attachment['file']);source=images.load_image(path)
        if min(source.size)<2:raise ValueError('This image is too small to crop. The original attachment is kept.')
        config={'source':url_for('creation.image_source',namespace=namespace,user=user,post_id=post_id,index=index),
                'mediaBase':url_for('draft_media',namespace=namespace,user=user,post_id=post_id,filename='placeholder').removesuffix('placeholder'),
                'saveUrl':url_for('creation.image_save',namespace=namespace,user=user,post_id=post_id,index=index),
                'sourceHash':images.digest(path),'revision':attachment_revision(attachment),
                'width':source.width,'height':source.height,'ratios':images.RATIOS,'labels':images.LABELS,
                'variants':attachment.get('variants',{}),'maxExport':images.MAX_EXPORT}
        return render_template('image_editor.html',**context(namespace,user,'compose'),post=post,index=index,
                               attachment=attachment,editor_config=config,embedded=request.args.get('embedded')=='1')

    @bp.get('/g/<namespace>/<user>/compose/<post_id>/media')
    def post_media(namespace,user,post_id):
        return render_template('post_media.html',namespace=namespace,user=user,post=store.draft(namespace,user,post_id))

    @bp.get('/g/<namespace>/<user>/images/<post_id>/<int:index>/source')
    def image_source(namespace,user,post_id,index):
        folder,post,attachment=draft_image(namespace,user,post_id,index)
        image=images.load_image(inside(folder,attachment['file']))
        buffer=BytesIO();image.save(buffer,'PNG');buffer.seek(0)
        return send_file(buffer,mimetype='image/png')

    @bp.post('/g/<namespace>/<user>/images/<post_id>/<int:index>/save')
    def image_save(namespace,user,post_id,index):
        try:
            recipes=json.loads(request.form.get('recipes','{}'))
        except (TypeError,ValueError):raise ValueError('Invalid image edits.')
        if not isinstance(recipes,dict):raise ValueError('Invalid image edits.')
        with store.garden_lock(namespace,user):
            folder,post,attachment=draft_image(namespace,user,post_id,index)
            if request.form.get('revision')!=attachment_revision(attachment):
                raise ValueError('Another editor changed this image. Reload before saving.')
            exports={key:request.files[key].stream for key in request.files}
            post=images.save_variants(folder,post,index,request.form.get('source_hash'),exports,recipes)
        return jsonify(variants=post['attachments'][index]['variants'],revision=attachment_revision(post['attachments'][index]))

    @bp.get('/g/<namespace>/<user>/tags/')
    def tag_browser(namespace,user):
        data=tags.catalogue(store,namespace,user);counts=tags.usage(store,namespace,user)
        groups=sorted({t['group'] for t in data['tags']},key=str.casefold)
        return render_template('tags.html',**context(namespace,user,'tags'),catalogue=data['tags'],counts=counts,groups=groups)

    @bp.post('/g/<namespace>/<user>/tags/save')
    def tag_save(namespace,user):
        with store.garden_lock(namespace,user):
            tag=tags.save_tag(store,namespace,user,request.form.get('name',''),request.form.get('group','Topics'),request.form.get('tag_id') or None)
        if request.accept_mimetypes.best=='application/json':return jsonify(tag)
        flash('Tag saved.','success')
        return redirect(url_for('creation.tag_browser',namespace=namespace,user=user))

    @bp.post('/g/<namespace>/<user>/tags/merge')
    def tag_merge(namespace,user):
        with store.garden_lock(namespace,user):
            tags.merge_tags(store,namespace,user,request.form.get('source'),request.form.get('target'))
        flash('Tags merged.','success')
        return redirect(url_for('creation.tag_browser',namespace=namespace,user=user))

    app.register_blueprint(bp)
