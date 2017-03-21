#!/bin/bash
REPOS="aosc-os-core aosc-os-abbs"
ABBS_META="../../abbs-meta/abbsmeta.py"
cd data/
for repo in $REPOS; do
    if [ ! -d $repo ]; then
        git clone https://github.com/AOSC-Dev/$repo.git
    fi
    python3 $ABBS_META abbs.db $repo/ $repo
done
python3 ../dpkgrepo.py abbs.db
