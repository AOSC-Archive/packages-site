cmake_minimum_required(VERSION 3.14)
cmake_policy(SET CMP0048 NEW)

project(packages-site VERSION 3.1.2)
set(LIBEXEC_PATH "libexec/packages-site")

find_package(SQLite3 REQUIRED)

add_library(mod_vercomp MODULE vercomp.c)
SET_TARGET_PROPERTIES(mod_vercomp PROPERTIES PREFIX "")
add_executable(dbhash dbhash.c)
target_link_libraries(dbhash SQLite::SQLite3)

add_subdirectory(static)
add_subdirectory(views)
add_subdirectory(tools)

install(TARGETS dbhash DESTINATION bin)
install(TARGETS mod_vercomp DESTINATION ${LIBEXEC_PATH} PERMISSIONS OWNER_WRITE WORLD_READ WORLD_EXECUTE)
install(FILES
    bottle_sqlite.py
    debian_support.py
    rawquery.py
    utils.py
    main.py
    DESTINATION ${LIBEXEC_PATH}
)

# CPack settings
set(CPACK_PACKAGE_VENDOR "AOSC")
set(CPACK_PACKAGE_DESCRIPTION "Package list website")
set(CPACK_DEBIAN_PACKAGE_MAINTAINER "AOSC Infra Team <infra@lists.aosc.io>")
set(CPACK_DEBIAN_PACKAGE_DEPENDS "python3, python3-bottle, python3-jinja2, python3-bs4, python3-requests, python3-psycopg2, python3-debian, python3-pyparsing, python3-html5lib, python3-feedparser, git, fossil")
set(CPACK_GENERATOR DEB TXZ)
set(CPACK_PACKAGE_CHECKSUM SHA256)
include(CPack)
