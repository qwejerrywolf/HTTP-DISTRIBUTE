"""Fixed inline icons: filenames choose a category, never SVG content."""
from pathlib import PurePath

GROUPS = {
    'text': 'txt md markdown log rst rtf tex srt ass vtt lrc',
    'code': 'py js ts jsx tsx html htm css scss json jsonl xml yaml yml toml ini cfg conf csv sql sh bat ps1 c cpp h java go rs',
    'image': 'jpg jpeg png gif webp bmp svg ico tif tiff heic heif avif raw psd',
    'audio': 'mp3 wav flac aac m4a ogg opus wma aiff ape mid midi',
    'video': 'mp4 mkv avi mov webm wmv flv m4v mpg mpeg ts m2ts 3gp',
    'pdf': 'pdf',
    'document': 'doc docx odt pages',
    'sheet': 'xls xlsx ods numbers',
    'slides': 'ppt pptx odp key',
    'archive': 'zip rar 7z tar gz bz2 xz iso',
}
PATHS = {
    'file': '<path d="M13 3H6v18h12V8zM13 3v5h5"/>',
    'folder': '<path d="M3 6h7l2 2h9v12H3z"/>',
    'text': '<path d="M13 3H6v18h12V8zM13 3v5h5M9 12h6M9 15h6M9 18h4"/>',
    'code': '<path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16"/>',
    'image': '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 18 5-5 3 3 5-6 5 7"/>',
    'audio': '<path d="M10 18V5l10-2v13M10 8l10-2"/><ellipse cx="7" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/>',
    'video': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4zM6 4v16M18 4v16"/>',
    'pdf': '<path d="M13 3H6v18h12V8zM13 3v5h5M9 17l3-6 3 6M10 15h4"/>',
    'document': '<path d="M13 3H6v18h12V8zM13 3v5h5M8 12l2 5 2-4 2 4 2-5"/>',
    'sheet': '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 9v12"/>',
    'slides': '<path d="M3 4h18v13H3zM12 17v4m-4 0 4-4 4 4M7 13V9m5 4V7m5 6v-3"/>',
    'archive': '<path d="M4 4h16v17H4zM4 8h16M10 4v4m4 0v4m-4 0v4m4 0v3"/>',
}
LABELS = dict(file='文件', folder='文件夹', text='文本', code='代码 / 数据', image='图片', audio='音频', video='视频', pdf='PDF', document='文档', sheet='表格', slides='演示文稿', archive='压缩包 / 镜像')


def file_type(name, kind='file'):
    if kind == 'folder':
        return 'folder'
    ext = PurePath(name).suffix.lower().lstrip('.')
    # Transport-stream .ts uses the video icon; TypeScript shares this extension.
    return next((group for group, extensions in reversed(list(GROUPS.items())) if ext in extensions.split()), 'file')


def icon_svg(name, kind='file'):
    category = file_type(name, kind)
    return f'<svg class="file-icon icon-{category}" role="img" aria-label="{LABELS[category]}" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{PATHS[category]}</svg>'
