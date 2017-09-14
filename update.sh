#!/bin/bash
REPOS="aosc-os-core aosc-os-abbs aosc-os-arm-bsps"
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ABBS_META="$DIR/../abbs-meta/abbsmeta.py"
ANITYA="$DIR/../abbs-meta/anitya.py"
DATA_DIR="data/"
cd "$DATA_DIR"
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
python3 $ABBS_META -p . -m . -d abbs.db -b staging,master,bugfix -B staging \
    -c base -u 'https://github.com/AOSC-Dev/aosc-os-abbs' -P 1 aosc-os-abbs
python3 $ABBS_META -p . -m . -d abbs.db -b master -B master \
    -c bsp -u 'https://github.com/AOSC-Dev/aosc-os-arm-bsps' -P 2 aosc-os-arm-bsps
python3 "$DIR/dpkgrepo.py" abbs.db
python3 $ANITYA -d abbs.db anitya.ini
