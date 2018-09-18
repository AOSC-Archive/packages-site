CC = gcc-8
CFLAGS = -fPIC -shared -O2 -Wall -Wextra

libsqlitefunctions.so:
	$(CC) $(CFLAGS) vercomp.c -o mod_vercomp.so
