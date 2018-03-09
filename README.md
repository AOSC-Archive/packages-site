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

Add `?type=json` to (almost) every endpoints, or send the `X-Requested-With: XMLHttpRequest` HTTP header, then you will get an json response.

On listings that have multiple pages, use `?page=n` to get each page.
Use `?page=all` to avoid paging. The `/list.json` gives a full list of packages.

You can download the [abbs-meta](https://github.com/AOSC-Dev/abbs-meta) SQLite database from `/data/abbs.db`.
