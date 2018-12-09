CC = gcc
CFLAGS = -fPIC -shared -O2 -Wall -Wextra

mod_vercomp.so: vercomp.c
	$(CC) $(CFLAGS) vercomp.c -o mod_vercomp.so
