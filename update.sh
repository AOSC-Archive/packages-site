#!/bin/bash
REPOS="aosc-os-core aosc-os-abbs aosc-os-arm-bsps"
ABBS_META="../../abbs-meta/abbsmeta.py"
cd data/
for repo in $REPOS; do
    if [ ! -d $repo ]; then
        git clone https://github.com/AOSC-Dev/$repo.git
    else
        pushd $repo
        git fetch --all
        git reset --hard origin/master
        git pull
        popd
    fi
    python3 $ABBS_META abbs.db $repo/ $repo
done
python3 ../dpkgrepo.py abbs.db
