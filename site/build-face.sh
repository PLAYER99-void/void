#!/bin/sh
# site/face.html is the app's own page (app/static/index.html) with every call to his server made to just wait, so the
# website can run his real face with no server behind it. Built from the app, never edited by hand.
cd "$(dirname "$0")/.." || exit 1
{ sed -n '1,/<head>/p' app/static/index.html
  echo '<script>/* the website copy of his page: no server here, so every call to it just waits (no errors, no network) */window.fetch=function(){return new Promise(function(){})};</script>'
  sed '1,/<head>/d' app/static/index.html; } > site/face.html
