file(CREATE_LINK "../../mod_vercomp.so" "${CMAKE_CURRENT_BINARY_DIR}/mod_vercomp.so" SYMBOLIC)

install(FILES
    piss/anitya.py
    piss/htmllistparse.py
    piss/piss.py
DESTINATION "${LIBEXEC_PATH}/tools/piss/")

install(FILES
    abbs-meta/abbsmeta.py
    abbs-meta/bashvar.py
    abbs-meta/fossil.py
    abbs-meta/reposync.py
DESTINATION "${LIBEXEC_PATH}/tools/abbs-meta/")

install(FILES
    dpkgrepo-meta/deb822.py
    dpkgrepo-meta/dpkgrepo.py
    dpkgrepo-meta/update.sh
    "${CMAKE_CURRENT_BINARY_DIR}/mod_vercomp.so"
DESTINATION "${LIBEXEC_PATH}/tools/dpkgrepo-meta/")
