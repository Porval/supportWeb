"""Deployment-specific identity and HTTPS origin for the hosted workbench."""
import os
import re
from urllib.parse import urlsplit

ADMIN_PHONE = os.environ.get('WORKBENCH_ADMIN_PHONE', '')
if not re.fullmatch(r'1[3-9][0-9]{9}', ADMIN_PHONE):
    raise ValueError('Set WORKBENCH_ADMIN_PHONE to the provisioned administrator phone number')

PUBLIC_ORIGIN = os.environ.get('WORKBENCH_PUBLIC_ORIGIN', '').rstrip('/')
_site = urlsplit(PUBLIC_ORIGIN)
if (_site.scheme != 'https' or not _site.hostname or _site.username is not None
        or _site.password is not None or _site.path or _site.query or _site.fragment):
    raise ValueError('Set WORKBENCH_PUBLIC_ORIGIN to the public HTTPS origin')
_site.port  # Validate the optional port before accepting requests.
PUBLIC_HOSTS = frozenset((_site.hostname, _site.netloc))
