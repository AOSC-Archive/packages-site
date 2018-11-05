#!/usr/bin/tclsh
package require uri
package require tls
package require http
package require tdom
package require sqlite3
::http::register https 443 [list ::tls::socket -tls1 1]
set ::http::defaultCharset utf-8

set hostname {[::1]}
set port 18082
set servhost "$hostname:18082"
set pserver [exec > /dev/null 2> /dev/null uwsgi_python3 --http-socket $servhost --wsgi-file main.py &]
puts "server pid $pserver"

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
    set doc [dom parse -html5 -ignorexmlns $data]
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

exec kill $pserver
