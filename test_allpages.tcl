#!/usr/bin/tclsh
package require uri
package require http
package require tdom
package require sqlite3
set ::http::defaultCharset utf-8

set fastcheck [expr {[lindex $argv 0] eq "--fast"}]
if {$fastcheck} {
    puts "fast check enabled"
}

set hostname {[::1]}
set port 18082
set servhost "$hostname:18082"
set masterfifo "/tmp/uwsgiservtest.fifo"
set pserver [exec > /dev/null 2> /dev/null uwsgi_python37 --http-socket $servhost --master-fifo $masterfifo --wsgi-file main.py &]
puts "started server $pserver"

set urlhost http://$servhost
set urlmatch [regsub -all {([\[\]])} $urlhost {\\\1}]
append urlmatch /*

sqlite3 db {:memory:}
db eval {CREATE TABLE links(url TEXT PRIMARY KEY, ref TEXT, status INTEGER)}
db eval {INSERT INTO links VALUES ('/', null, null)}

after 1000

set testpath /

while {$testpath ne ""} {
    set row [db eval {
        SELECT url, ref FROM links
        WHERE status IS null ORDER BY random() LIMIT 1
    }]
    set testpath [lindex $row 0]
    if {$fastcheck && [regexp "^/(packages|changelog|revdep|qa)/" $testpath]} {
        db eval {UPDATE links SET status=-1 WHERE url=$testpath}
        continue
    }
    set testref [lindex $row 1]
    set token [::http::geturl $urlhost$testpath -timeout 5000]
    set ncode [::http::ncode $token]
    db eval {UPDATE links SET status=$ncode WHERE url=$testpath}
    upvar #0 $token state
    if {$ncode >= 400} {
        puts "$ncode $testpath ref $testref"
        ::http::cleanup $token
        continue
    }
    if {! [string match text/html* $state(type)]} {
        ::http::cleanup $token
        continue
    }
    set data [::http::data $token]
    set doc [dom parse -html $data]
    foreach tag [domDoc $doc getElementsByTagName a] {
        set href [domNode $tag getAttribute href {}]
        set href [::uri::resolve $urlhost$testpath $href]
        if {[string match $urlmatch $href]} {
            set href [string range $href [string length $urlhost] end]
            db eval {INSERT OR IGNORE INTO links VALUES ($href, $testpath, null)}
        }
    }
    $doc delete
    ::http::cleanup $token
}

puts "=== Summary ==="
db eval {
    SELECT status, count(url) cnt FROM links GROUP BY status ORDER BY status
} values {
    if {$values(status) == -1} {
        puts "ignored: $values(cnt) pages"
    } else {
        puts "$values(status): $values(cnt) pages"
    }
}
set mf [open $masterfifo w]
puts $mf Q
after 1000
file delete $masterfifo
