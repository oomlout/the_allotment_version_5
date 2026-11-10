"""Non-destructive image variants; browser edits and offline legacy backfill.

Originals are immutable. Each save writes new JPEGs then atomically replaces the
post manifest. Existing queue snapshots continue to point at their old files.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from .storage import inside, now, write_yaml

RATIOS={'square':(1,1),'portrait':(4,5),'landscape':(16,9),'story':(9,16)}
LABELS={'square':'1:1 · Square','portrait':'4:5 · Portrait','landscape':'16:9 · Landscape','story':'9:16 · Story'}
MAX_PIXELS=40_000_000
MAX_EXPORT=2048


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_image(source):
    try:
        with Image.open(source) as image:
            if image.width*image.height>MAX_PIXELS:raise ValueError('Image exceeds 40 megapixels.')
            if getattr(image,'is_animated',False):raise ValueError('Animated images stay unchanged. Choose a still image for cropping.')
            image.load()
            image=ImageOps.exif_transpose(image).convert('RGBA')
            background=Image.new('RGB',image.size,'white');background.paste(image,mask=image.getchannel('A'))
            return background
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
        raise ValueError('Choose a readable still image.') from error


def media_files(attachment):
    return list(dict.fromkeys([attachment['file'],*([attachment['original_file']] if attachment.get('original_file') else []),*[v['file'] for v in attachment.get('variants',{}).values()]]))


def display_file(attachment):
    return attachment.get('variants',{}).get(attachment.get('display_variant'),{}).get('file',attachment['file'])


def checked_attachment(post, index):
    if index<0 or index>=len(post.get('attachments',[])):raise ValueError('Image not found.')
    attachment=post['attachments'][index]
    if attachment['type']!='image':raise ValueError('This editor supports still images. Video files stay unchanged.')
    return attachment


def validate_recipe(recipe):
    if not isinstance(recipe,dict) or recipe.get('engine')!='cropperjs-1':raise ValueError('Invalid crop recipe.')
    if len(json.dumps(recipe))>8000:raise ValueError('Crop recipe is too large.')
    crop=recipe.get('crop',{});adjust=recipe.get('adjustments',{})
    if not isinstance(crop,dict) or not isinstance(adjust,dict):raise ValueError('Invalid crop recipe.')
    for key in ('x','y','width','height','rotate','scaleX','scaleY'):
        value=crop.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>100000:
            raise ValueError('Invalid crop coordinates.')
    if crop['width']<=0 or crop['height']<=0 or crop['rotate']%90 or crop['scaleX'] not in (-1,1) or crop['scaleY'] not in (-1,1):
        raise ValueError('Invalid crop transform.')
    for key in ('brightness','contrast','saturation'):
        value=adjust.get(key)
        minimum=0 if key=='saturation' else .5
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not minimum<=value<=1.5:
            raise ValueError('Image adjustment is outside the supported range.')
    return deepcopy(recipe)


def save_variants(folder, post, index, source_hash, exports, recipes):
    attachment=checked_attachment(post,index)
    source=inside(folder,attachment['file'])
    if digest(source)!=source_hash:raise ValueError('The original changed. Reload the editor before saving.')
    if set(exports)!=set(recipes) or not exports or not set(exports)<=set(RATIOS):raise ValueError('Choose supported crop versions.')
    prepared=[]
    for key,stream in exports.items():
        recipe=validate_recipe(recipes[key])
        image=load_image(stream)
        a,b=RATIOS[key]
        if max(image.size)>MAX_EXPORT or abs(image.width*b-image.height*a)>max(a,b):
            raise ValueError('Export size does not match the selected aspect ratio.')
        prepared.append((key,image,recipe))
    result=deepcopy(post);target=result['attachments'][index]
    variants=target.setdefault('variants',{})
    for key,image,recipe in prepared:
        relative=f"media/variants/{hashlib.sha256(attachment['file'].encode()).hexdigest()[:12]}/{key}-{uuid.uuid4().hex[:12]}.jpg"
        output=inside(folder,relative);output.parent.mkdir(parents=True,exist_ok=True)
        image.save(output,'JPEG',quality=92,optimize=True)
        variants[key]={'file':relative,'width':image.width,'height':image.height,'source_sha256':source_hash,
                       'recipe':recipe,'created_at':now(),'needs_review':False}
    result['updated_at']=now();result.pop('path',None)
    write_yaml(Path(folder)/'post.yaml',result)
    return result


def generate_variants(folder, post, index):
    """Fill missing ratios with neutral, centred crops. Never overwrite edits."""
    attachment=checked_attachment(post,index);source=inside(folder,attachment['file'])
    image=load_image(source);source_hash=digest(source)
    result=deepcopy(post);variants=result['attachments'][index].setdefault('variants',{})
    for key,(a,b) in RATIOS.items():
        if key in variants:continue
        w,h=image.size
        crop_w=min(w,h*a/b);crop_h=crop_w*b/a
        x,y=(w-crop_w)/2,(h-crop_h)/2
        scale=min(1,MAX_EXPORT/max(crop_w,crop_h))
        size=(max(1,round(crop_w*scale)),max(1,round(crop_h*scale)))
        cropped=image.resize(size,Image.Resampling.LANCZOS,box=(x,y,x+crop_w,y+crop_h))
        relative=f"media/variants/{hashlib.sha256(attachment['file'].encode()).hexdigest()[:12]}/{key}-{uuid.uuid4().hex[:12]}.jpg"
        output=inside(folder,relative);output.parent.mkdir(parents=True,exist_ok=True)
        cropped.save(output,'JPEG',quality=92,optimize=True)
        variants[key]={'file':relative,'width':size[0],'height':size[1],'source_sha256':source_hash,
            'created_at':now(),'needs_review':True,'recipe':{'engine':'cropperjs-1','method':'centre',
            'crop':{'x':x,'y':y,'width':crop_w,'height':crop_h,'rotate':0,'scaleX':1,'scaleY':1},
            'adjustments':{'brightness':1,'contrast':1,'saturation':1}}}
    result.pop('path',None)
    write_yaml(Path(folder)/'post.yaml',result)
    return result
