# packages-site
Package list website. https://packages.aosc.io/

## Deploy

```
git clone https://github.com/AOSC-Dev/abbsmeta.git
git clone https://github.com/AOSC-Dev/packages-site.git
cd packages-site
pip3 install -r requirements.txt
bash ./update.sh
```

Then use your WSGI compatible web servers.

## API

Add `?type=json` to (almost) every endpoints.
Or send the `X-Requested-With: XMLHttpRequest` HTTP header.

The `/list.json` gives a list of packages.
