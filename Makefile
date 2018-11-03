CC = gcc
CFLAGS = -fPIC -shared -O2 -Wall -Wextra

mod_vercomp.so:
	$(CC) $(CFLAGS) vercomp.c -o mod_vercomp.so
