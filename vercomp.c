#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include "sqlite3ext.h"
SQLITE_EXTENSION_INIT1

#define UNUSED(x) (void)(x)

/*
 * Implementation of the "vercomp" collation.
 * This collation sorts TEXT using Debian version comparison rules.
 */

typedef struct dpkg_version {
    long epoch;
    const char* version;
    const char* revision;
} dpkg_version_t;

static dpkg_version_t parse_version(char *string) {
    dpkg_version_t version = {0, "", ""};
    char *colon, *hyphen;
    colon = strchr(string, ':');
    if (colon) {
        version.epoch = strtol(string, NULL, 10);
        string = colon+1;
    }
    version.version = string;
    hyphen = strrchr(string, '-');
    if (hyphen) {
        *hyphen++ = 0;
        version.revision = hyphen;
    }
    return version;
}

static int order(int c) {
	if (isdigit(c)) {
		return 0;
	} else if (isalpha(c)) {
		return c;
	} else if (c == '~') {
		return -1;
	} else if (c) {
		return c + 256;
	} else {
		return 0;
    }
}

int version_compare(const char *a, const char *b) {
    while (*a || *b) {
        int first_diff = 0;

        while ((*a && !isdigit(*a)) || (*b && !isdigit(*b))) {
            int ac = order(*a);
            int bc = order(*b);

            if (ac != bc)
                return ac - bc;

            a++;
            b++;
        }
        while (*a == '0')
            a++;
        while (*b == '0')
            b++;
        while (isdigit(*a) && isdigit(*b)) {
            if (!first_diff)
                first_diff = *a - *b;
            a++;
            b++;
        }

        if (isdigit(*a))
            return 1;
        if (isdigit(*b))
            return -1;
        if (first_diff)
            return first_diff;
    }
    return 0;
}

static int vercomp_collation(
    void *pArg, int nSa, const void *bSa, int nSb, const void *bSb
){
    char *svera, *sverb;
    dpkg_version_t vera, verb;
    int comp;
    UNUSED(pArg);
    svera = (char *)malloc(nSa + 1);
    sverb = (char *)malloc(nSb + 1);
    svera[nSa] = sverb[nSb] = 0;
    memcpy(svera, bSa, nSa);
    memcpy(sverb, bSb, nSb);
    vera = parse_version(svera);
    verb = parse_version(sverb);

    if (vera.epoch < verb.epoch) {
        return -1;
    } else if (vera.epoch > verb.epoch) {
        return 1;
    }
    comp = version_compare(vera.version, verb.version);
    if (comp) return comp;
    comp = version_compare(vera.revision, verb.revision);
    if (comp) return comp;
    return strcmp(svera, sverb);
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_modvercomp_init(
    sqlite3 *db,
    char **pzErrMsg,
    const sqlite3_api_routines *pApi
){
    int rc = SQLITE_OK;
    SQLITE_EXTENSION_INIT2(pApi);
    UNUSED(pzErrMsg);
    rc = sqlite3_create_collation(db, "vercomp",
                                  SQLITE_UTF8, NULL, vercomp_collation);
    return rc;
}

