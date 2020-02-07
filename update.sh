#!/bin/bash
REPOS="aosc-os-core aosc-os-abbs aosc-os-arm-bsps"
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ABBS_META="$DIR/../abbs-meta/abbsmeta.py"
DATA_DIR="data/"
if [ ! -d "$DATA_DIR" ]; then mkdir -p "$DATA_DIR"; fi
pushd "$DATA_DIR"
if [ ! -f .gitkeep ]; then touch .gitkeep; fi
for repo in $REPOS; do
    if [ ! -d $repo.git ]; then
        git clone --mirror https://github.com/AOSC-Dev/$repo.git
    else
        pushd $repo.git
        git remote update
        popd
    fi
done
python3 $ABBS_META -p . -m . -d abbs.db -b master -B master \
    -c base -u 'https://github.com/AOSC-Dev/aosc-os-core' -P 0 aosc-os-core
python3 $ABBS_META -p . -m . -d abbs.db \
    -b stable,stable-proposed,testing,testing-proposed,explosive -B testing-proposed \
    -c base -u 'https://github.com/AOSC-Dev/aosc-os-abbs' -P 1 aosc-os-abbs
python3 $ABBS_META -p . -m . -d abbs.db -b master -B master \
    -c bsp -u 'https://github.com/AOSC-Dev/aosc-os-arm-bsps' -P 2 aosc-os-arm-bsps
pushd "$DIR"
if [ ! -f mod_vercomp.so ]; then make mod_vercomp.so; fi
if [ ! -f dbhash ]; then make dbhash; fi
popd
python3 "$DIR/dpkgrepo.py" abbs.db
rm -rf cache.new
mkdir -p cache.new
dbs="abbs.db piss.db"
for repo in $REPOS; do dbs+=" $repo-marks.db"; done
pushd cache.new
for db in $dbs; do
    sqlite3 ../$db ".backup $db"
    stat --printf="%s " $db >> dbhashs
    "$DIR/dbhash" $db >> dbhashs
    gzip -9 --rsyncable $db
done
popd
mv cache cache.old
mv cache.new cache
rm -rf cache.old
