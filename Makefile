CFLAGS = -fPIC -O2 -Wall -Wextra

all: mod_vercomp.so dbhash

mod_vercomp.so: vercomp.c
	$(CC) $(CFLAGS) -shared vercomp.c -o mod_vercomp.so

dbhash: dbhash.c
	$(CC) $(CFLAGS) dbhash.c -o dbhash -lsqlite3

clean:
	-rm -f mod_vercomp.so dbhash
