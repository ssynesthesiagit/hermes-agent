package com.hermes.gatewayclient;

/** JavaScript helpers scoped to the hosted Hermes mobile chat UI. */
final class SpeechPageScript {

    private SpeechPageScript() {}

    static String hasComposer() {
        return "Boolean(document.querySelector('.mobile-console__chat-panel "
            + ".mobile-console__composer textarea[aria-label^=\"Message \"]:not([disabled])'))";
    }

    static String insertTranscript(String transcript) {
        return "(function(){"
            + "var input=document.querySelector('.mobile-console__chat-panel "
            + ".mobile-console__composer textarea[aria-label^=\"Message \"]:not([disabled])');"
            + "if(!input){return 'missing-composer';}"
            + "var phrase=" + quote(transcript.trim()) + ";"
            + "if(!phrase){return 'empty';}"
            + "var current=input.value||'';"
            + "var focused=document.activeElement===input;"
            + "var start=focused&&Number.isInteger(input.selectionStart)?input.selectionStart:current.length;"
            + "var end=focused&&Number.isInteger(input.selectionEnd)?input.selectionEnd:start;"
            + "var before=current.slice(0,start);var after=current.slice(end);"
            + "var left=before&&!/\\s$/.test(before)?' ':'';"
            + "var right=after&&!/^\\s/.test(after)?' ':'';"
            + "var inserted=left+phrase+right;var next=before+inserted+after;"
            + "var setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;"
            + "setter.call(input,next);"
            + "input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:phrase}));"
            + "input.dispatchEvent(new Event('change',{bubbles:true}));"
            + "var caret=before.length+inserted.length;input.focus();"
            + "try{input.setSelectionRange(caret,caret);}catch(ignored){}"
            + "return 'inserted';"
            + "})()";
    }

    /**
     * Returns [contextId, replyId, replyText, streaming] or null. Context and
     * reply IDs are opaque hashes and never contain the conversation text.
     */
    static String latestReply() {
        return "(function(){"
            + "var panel=document.querySelector('.mobile-console__chat-panel');"
            + "if(!panel){return null;}"
            + "var bot=document.querySelector('#mobile-chat-title');"
            + "var context=(location.origin||'')+'|'+(bot?bot.textContent.trim():'bot');"
            + "var messages=Array.from(panel.querySelectorAll('.mobile-message'));"
            + "var assistants=messages.filter(function(row){return row.classList.contains('mobile-message--assistant');});"
            + "if(!assistants.length){return [context,'','',false];}"
            + "var row=assistants[assistants.length-1];"
            + "var body=Array.from(row.children).find(function(child){return !child.classList.contains('mobile-message__meta')&&!child.classList.contains('mobile-message__actions');});"
            + "var text=(body?(body.innerText||body.textContent||''):'').replace(/[ \\t]+/g,' ').replace(/\\n{3,}/g,'\\n\\n').trim();"
            + "var streaming=Boolean(row.querySelector('.mobile-message__cursor,[aria-label=\"Streaming response\"]'));"
            + "var signature=messages.map(function(item){return item.className+'\\n'+item.textContent;}).join('\\u001e');"
            + "var hash=2166136261;for(var i=0;i<signature.length;i++){hash^=signature.charCodeAt(i);hash=Math.imul(hash,16777619);}"
            + "var replyId=messages.length+'|'+assistants.length+'|'+(hash>>>0).toString(16);"
            + "return [context,replyId,text,streaming];"
            + "})()";
    }

    static String quote(String value) {
        StringBuilder quoted = new StringBuilder(value.length() + 16).append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '\\':
                    quoted.append("\\\\");
                    break;
                case '"':
                    quoted.append("\\\"");
                    break;
                case '\b':
                    quoted.append("\\b");
                    break;
                case '\f':
                    quoted.append("\\f");
                    break;
                case '\n':
                    quoted.append("\\n");
                    break;
                case '\r':
                    quoted.append("\\r");
                    break;
                case '\t':
                    quoted.append("\\t");
                    break;
                default:
                    if (character < 0x20 || character == '\u2028' || character == '\u2029') {
                        quoted.append(String.format("\\u%04x", (int) character));
                    } else {
                        quoted.append(character);
                    }
            }
        }
        return quoted.append('"').toString();
    }
}
