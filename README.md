# packages-site
Package list website. https://packages.aosc.io/

For more information, see: https://wiki.aosc.io/developers/packages-site

## Dependencies

* C compiler (gcc)
* git
* fossil
* `requirements.txt`
* (for testing) tcl, tdom, tcl sqlite binding

## Deploy

```
git clone https://github.com/AOSC-Dev/abbs-meta.git
git clone https://github.com/AOSC-Dev/packages-site.git
cd packages-site
sudo apt install libsqlite3-dev
make
pip3 install -r requirements.txt
bash ./update.sh
```

Then use your WSGI compatible web servers.

## API

Add `?type=json` to (almost) every endpoints, or send the `X-Requested-With: XMLHttpRequest` HTTP header, then you will get an json response.

Add `?type=tsv` to endpoints with a large table, then you will get a Tab-separated Values table, suitable for spreadsheet applications or unix tools.

On listings that have multiple pages, use `?page=n` to get each page.
Use `?page=all` to avoid paging. For example, use `?page=all&type=tsv` to get a full listing in TSV.

The `/list.json` gives a full list of packages.

You can download the [abbs-meta](https://github.com/AOSC-Dev/abbs-meta) SQLite database from `/data/abbs.db`.
