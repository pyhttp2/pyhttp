// Everything pyhttp's editor needs from CodeMirror 6, exposed as window.CM.
export { EditorState, Compartment, StateEffect, StateField, RangeSet } from "@codemirror/state";
export { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection,
  dropCursor, rectangularSelection, crosshairCursor, highlightSpecialChars, placeholder, Decoration, gutter, GutterMarker } from "@codemirror/view";
export { defaultKeymap, history, historyKeymap, indentWithTab, toggleComment, toggleLineComment, toggleBlockComment,
  insertNewlineAndIndent, indentMore, indentLess, selectAll, undo, redo } from "@codemirror/commands";
export { foldGutter, foldKeymap, foldAll, unfoldAll, foldCode, unfoldCode, indentOnInput, bracketMatching,
  syntaxHighlighting, HighlightStyle, defaultHighlightStyle, indentUnit, StreamLanguage, LanguageSupport, language } from "@codemirror/language";
export { searchKeymap, highlightSelectionMatches, openSearchPanel, search } from "@codemirror/search";
export { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
export { linter, lintGutter, setDiagnostics, lintKeymap } from "@codemirror/lint";
export { json, jsonLanguage } from "@codemirror/lang-json";
export { javascript, javascriptLanguage } from "@codemirror/lang-javascript";
export { python, pythonLanguage } from "@codemirror/lang-python";
export { xml, xmlLanguage } from "@codemirror/lang-xml";
export { html, htmlLanguage } from "@codemirror/lang-html";
export { tags } from "@lezer/highlight";
