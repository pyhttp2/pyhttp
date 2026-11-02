# Editor bundle

`pyhttp/static/vendor/codemirror.js` is CodeMirror 6 (MIT) bundled into one file so the
app needs no Node at runtime. Rebuild only when upgrading CodeMirror:

    cd packaging/editor
    npm install
    npm run build

Commit the resulting `codemirror.js`. Licenses: CodeMirror and Lezer are MIT, see
THIRD-PARTY-NOTICES.md.
