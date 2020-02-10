#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
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
    dpkg_version_t version = {0, "", "0"};
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

static int version_compare(const char *a, const char *b) {
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

static int dpkg_version_compare(char *svera, char *sverb){
    dpkg_version_t vera, verb;
    int comp;
    vera = parse_version(svera);
    verb = parse_version(sverb);

    if (vera.epoch < verb.epoch) {
        return -1;
    } else if (vera.epoch > verb.epoch) {
        return 1;
    }
    comp = version_compare(vera.version, verb.version);
    if (comp) return comp;
    return version_compare(vera.revision, verb.revision);
}

/* SQLite collation: vercomp */
static int vercomp_collation(
    void *pArg, int nSa, const void *bSa, int nSb, const void *bSb
){
    char *svera, *sverb;
    int comp;
    UNUSED(pArg);
    svera = (char *)malloc(nSa + 1);
    sverb = (char *)malloc(nSb + 1);
    svera[nSa] = sverb[nSb] = 0;
    memcpy(svera, bSa, nSa);
    memcpy(sverb, bSb, nSb);
    comp = dpkg_version_compare(svera, sverb);
    if (comp == 0) {
        memcpy(svera, bSa, nSa);
        memcpy(sverb, bSb, nSb);
        comp = strcmp(svera, sverb);
    }
    free(svera);
    free(sverb);
    return comp;
}

/* SQLite function:
 * compare_dpkgrel(version_a, operator, version_b) -> int
 */
static void compare_dpkgrel(
    sqlite3_context *ctx, int argc, sqlite3_value **argv
){
    if (argc != 3) {
        sqlite3_result_null(ctx);
        return;
    }
    if (sqlite3_value_type(argv[0]) == SQLITE_NULL) {
        sqlite3_result_null(ctx);
        return;
    }
    if (sqlite3_value_type(argv[1]) == SQLITE_NULL) {
        sqlite3_result_int(ctx, 1);
        return;
    }
    if (sqlite3_value_type(argv[2]) == SQLITE_NULL) {
        sqlite3_result_null(ctx);
        return;
    }

    int nver1, nver2;
    char *sver1, *sver2;
    int cmp_result, result;

    const char *pver1 = (const char *)sqlite3_value_text(argv[0]);
    nver1 = strlen(pver1);
    sver1 = (char *)malloc(nver1);
    memcpy(sver1, pver1, nver1);

    const char *pver2 = (const char *)sqlite3_value_text(argv[2]);
    nver2 = strlen(pver2);
    sver2 = (char *)malloc(nver2);
    memcpy(sver2, pver2, nver2);

    cmp_result = vercomp_collation(NULL, nver1, sver1, nver2, sver2);

    free(sver1);
    free(sver2);

    const char *p_op = (const char *)sqlite3_value_text(argv[1]);

    /* < and > are actually <= and >= in dpkg.
     * Only <<, <=, =, >= and >> are actually allowed.
     * <, >, == are provided for compatibility.
     * https://www.debian.org/doc/debian-policy/ch-relationships.html
     */
    if (!strcmp(p_op, "=") || !strcmp(p_op, "==")) {
        result = (cmp_result == 0);
    } else if (!strcmp(p_op, "<<") || !strcmp(p_op, "<")) {
        result = (cmp_result < 0);
    } else if (!strcmp(p_op, "<=")) {
        result = (cmp_result <= 0);
    } else if (!strcmp(p_op, ">=")) {
        result = (cmp_result >= 0);
    } else if (!strcmp(p_op, ">>") || !strcmp(p_op, ">")) {
        result = (cmp_result > 0);
    } else if (p_op[0] == 0) {
        result = 1;
    } else {
        sqlite3_result_null(ctx);
        return;
    }
    sqlite3_result_int(ctx, result);
    return;
}


/* SQLite function: dpkg_version(version, release, epoch) -> text
 *
 * (CASE WHEN ifnull(epoch, '') = '' THEN ''
 *  ELSE epoch || ':' END) || version ||
 * (CASE WHEN ifnull(release, '') IN ('', '0') THEN ''
 *  ELSE '-' || release END)
 */

static void make_dpkg_version(
    sqlite3_context *ctx, int argc, sqlite3_value **argv
){
    if (argc != 3) {
        sqlite3_result_null(ctx);
        return;
    }
    if (sqlite3_value_type(argv[0]) == SQLITE_NULL) {
        sqlite3_result_null(ctx);
        return;
    }

    const char *p_version = (const char *)sqlite3_value_text(argv[0]);
    const char *p_release;
    const char *p_epoch;
    char *s_fullver;
    char *ptr;
    int n_version = strlen(p_version);
    int n_release = 0;
    int n_epoch = 0;
    int n_fullver = n_version;
    if (sqlite3_value_type(argv[1]) != SQLITE_NULL) {
        p_release = (const char *)sqlite3_value_text(argv[1]);
        n_release = strlen(p_release);
        if (n_release > 0 && strcmp(p_release, "0") == 0) {
            n_release = 0;
        }
    }
    if (sqlite3_value_type(argv[2]) != SQLITE_NULL) {
        p_epoch = (const char *)sqlite3_value_text(argv[2]);
        n_epoch = strlen(p_epoch);
    }
    if (n_release) {
        n_fullver += n_release + 1;
    }
    if (n_epoch) {
        n_fullver += n_epoch + 1;
    }
    s_fullver = (char *)malloc(n_fullver);
    ptr = s_fullver;
    if (n_epoch) {
        memcpy(ptr, p_epoch, n_epoch);
        ptr += n_epoch;
        *ptr = ':';
        ptr++;
    }
    memcpy(ptr, p_version, n_version);
    ptr += n_version;
    if (n_release) {
        *ptr = '-';
        ptr++;
        memcpy(ptr, p_release, n_release);
        ptr += n_release;
    }
    sqlite3_result_text(ctx, s_fullver, n_fullver, free);
    return;
}

int modvercomp_install(sqlite3 *db){
    int rc = SQLITE_OK;
    rc = sqlite3_create_collation(
        db, "vercomp", SQLITE_UTF8, NULL, vercomp_collation);
    if (rc) return rc;
    rc = sqlite3_create_function(
        db, "compare_dpkgrel", 3, SQLITE_UTF8 | SQLITE_DETERMINISTIC,
        NULL, compare_dpkgrel, NULL, NULL);
    if (rc) return rc;
    rc = sqlite3_create_function(
        db, "dpkg_version", 3, SQLITE_UTF8 | SQLITE_DETERMINISTIC,
        NULL, make_dpkg_version, NULL, NULL);
    return rc;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int sqlite3_modvercomp_init(
    sqlite3 *db,
    char **pzErrMsg,
    const sqlite3_api_routines *pApi
){
    SQLITE_EXTENSION_INIT2(pApi);
    UNUSED(pzErrMsg);
    return modvercomp_install(db);
}

