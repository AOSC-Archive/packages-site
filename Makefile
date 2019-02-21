CC = gcc
CFLAGS = -fPIC -O2 -Wall -Wextra

mod_vercomp.so: vercomp.c
	$(CC) $(CFLAGS) -shared vercomp.c -o mod_vercomp.so

dbhash: dbhash.c
	$(CC) $(CFLAGS) -lsqlite3 dbhash.c -o dbhash
