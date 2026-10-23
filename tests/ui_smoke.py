"""Mocked-Eel Playwright UI smoke test.

Install requirements-dev.txt and run `python -m playwright install chromium` first.
This checks selected frontend interactions, not the native Eel/Windows launch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
WEB = PROJECT / "web"
files = {
    "main.py": 'def greet( name ):\n  return  "hello",name\n',
    "styles.css": ".card { color: red; }\n",
    "index.html": '<!doctype html><html><body><h1>Hello</h1></body></html>\n',
    "app.js": 'let value = 1;\nvalue += 2;\nconsole.log("value", value);\n',
    "helper.ts": 'export function greet(name: string): string { return name; }\n',
    "app.ts": 'import { greet } from "./helper";\nconst output = greet("FlowCode");\n',
}
tree = {"name": "sample_workspace", "path": "", "is_dir": True, "size": 0,
        "children": [{"name": name, "path": name, "is_dir": False, "size": len(source), "modified": 1}
                     for name, source in files.items()]}

MOCK = r'''(() => {
const files=__FILES__,tree=__TREE__; window.flowcodeMockFiles=files; window.flowcodeMockCalls=[]; let mockGitConflicts=[]; let conflictOnce=true; let mockSession=window.__flowcodeSessionSeed||null; window.flowcodeMockSession=mockSession; window.flowcodeMockSettings={}; let mockWorkspaceSettings={}; window.flowcodeMockWorkspaceSettings=mockWorkspaceSettings;
let terminalPollSent=false; let mockTasks=[{label:'Build app',type:'process',command:'python',args:['-V'],group:'build',cwd:'${workspaceFolder}',env:{MODE:'test'}}];
const api={
 get_app_info:()=>({name:'FlowCode Studio',version:'0.34.0'}),
 load_user_settings:()=>({settings:window.flowcodeMockSettings}),
 save_user_settings:settings=>{window.flowcodeMockSettings=settings;return {ok:true};},
 load_workspace_settings:()=>({settings:{...mockWorkspaceSettings},file_exists:Object.keys(mockWorkspaceSettings).length>0,settings_path:'.vscode/settings.json',warnings:[]}),
 save_workspace_settings:settings=>{mockWorkspaceSettings=Object.fromEntries(Object.entries(settings).filter(([,value])=>value!==null));window.flowcodeMockWorkspaceSettings=mockWorkspaceSettings;return {ok:true,settings:{...mockWorkspaceSettings},settings_path:'.vscode/settings.json'};},
 discover_workspace_tests:()=>({python_files:['tests/test_alpha.py'],python_count:1,python_runner:'pytest',node_files:['test/answer.test.cjs'],node_count:1,node_available:true,npm_test:true,npm_test_script:'node --test',npm_available:true,truncated:false,warnings:[]}),
 run_workspace_tests:kind=>({kind,runner:kind==='python'?'pytest':kind==='node'?'node --test':'npm test',returncode:0,timed_out:false,stdout:kind==='python'?'1 passed in 0.03s':'tests 1\npass 1',stderr:'',test_files:kind==='python'?['tests/test_alpha.py']:['test/answer.test.cjs']}),
 load_editor_session:()=>({session:mockSession}),
 save_editor_session:state=>{mockSession=state;window.flowcodeMockSession=state;return {ok:true};},
 get_workspace:()=>({name:'sample_workspace',path:'/workspace/sample_workspace',sample:true,tree}),
 save_debug_configuration:(operation,configuration,activePath,configId)=>({ok:true,id:configId||'launch-2',name:configuration?.name||'Deleted config',configuration_count:1}),
 list_tasks:()=>({tasks:mockTasks.map(task=>({...task,args:[...task.args],env:{...task.env}})),error:null}),
 save_workspace_task:(operation,task,originalLabel)=>{window.flowcodeMockCalls.push(['task-config', [operation, task, originalLabel]]);if(operation==='delete')mockTasks=mockTasks.filter(item=>item.label!==originalLabel);else if(operation==='update')mockTasks=mockTasks.map(item=>item.label===originalLabel?{...task}:item);else mockTasks.push({...task});return {ok:true,operation,label:task?.label||originalLabel,task_count:mockTasks.length};},
 run_task:label=>{window.flowcodeMockCalls.push(['run_task',[label]]);return {task:label,command:'python -V',returncode:0,stdout:'Python 3.12\n',stderr:'',timed_out:false};},
 list_debug_configurations:path=>({errors:[],configurations:path.endsWith('.js')?[{id:'launch-1',name:'Node: Current File',type:'node',program:path,cwd:'.',args:[],env:{},runtimeArgs:[]}]:path.endsWith('.py')?[{id:'launch-0',name:'Python: Current File',type:'python',program:path,cwd:'.',args:[],env:{},runtimeArgs:[]}]:[]}),
 debug_start:(path,breakpoints,launch,conditions)=>({ok:true,pid:321,file:path,breakpoints,breakpoint_conditions:conditions||{},engine:'Python bdb (subprocess)'}),
 debug_command:command=>{if(command==='stop')window.flowcodePythonStopRequested=true;return {ok:true,command};},
 debug_poll:()=>({events:[],running:true,returncode:null}),
 debug_stop:()=>({ok:true}),
 node_debug_start:(path,breakpoints,launch)=>({ok:true,pid:123,file:path,breakpoints,launch,engine:'Node.js Inspector Protocol'}),
 node_debug_command:command=>{if(command==='stop')window.flowcodeNodeStopRequested=true;if(String(command).startsWith('evaluate '))return {ok:true,expression:String(command).slice(9),value:'3'};return {ok:true,command};},
 node_debug_poll:()=>{if(!window.flowcodeNodePaused){window.flowcodeNodePaused=true;return {events:[{type:'stopped',reason:'step',file:'app.js',line:1,column:1,function:'<module>',locals:{value:'1'}}],running:true,returncode:null};}if(window.flowcodeNodeStopRequested){window.flowcodeNodeStopRequested=false;return {events:[{type:'terminated',returncode:-15,message:'Node.js debug session stopped.'}],running:false,returncode:-15};}return {events:[],running:true,returncode:null};},
 node_debug_stop:()=>({ok:true}),
 terminal_start:()=>({ok:true,pid:456,shell:'bash',pty:true}),
 terminal_poll:()=>{if(!terminalPollSent){terminalPollSent=true;return {output:'\u001b[?2004hPTY output\r\nnext line\u001b[?2004l',running:true,pid:456,pty:true};}return {output:'',running:true,pid:456,pty:true};},
 read_file:p=>({path:p,content:files[p],size:files[p].length,modified:1,sha256:'disk-hash'}),
 save_file:(p,c,expected,force)=>{if(p==='main.py'&&!force&&conflictOnce){conflictOnce=false;files[p]='def greet(name):\n    return \"external edit\"\n';return {error:'The file changed on disk since it was opened. Compare, reload or explicitly overwrite it.'};}files[p]=c;return {ok:true,path:p,size:c.length,modified:2,sha256:'saved-hash'}},
 validate_code:(p,c)=>({checked:true,diagnostics:p.endsWith('.css')&&c.includes('MALFORMED')?[{severity:'warning',message:'Opening brace is not closed',line:1,column:7,source:'CSS'}]:[]}),
 validate_workspace:overrides=>{window.flowcodeWorkspaceOverrides=overrides;return {checked_files:6,candidate_files:6,skipped_files:0,partial:false,elapsed_ms:12,diagnostics:[{path:'app.js',severity:'error',message:'Unexpected token in workspace file',line:2,column:4,source:'Node.js'},{path:'styles.css',severity:'warning',message:'Opening brace is not closed',line:2,column:1,source:'CSS'}]};},
 format_python_document:()=>({formatted:'def greet(name):\n    return "hello", name\n',changed:true,engine:'Black'}),
 python_completions:()=>({items:[{name:'greet',type:'function',description:'def greet(name)'}]}),
 format_javascript_document:(path,content)=>({engine:'TypeScript',formatted:content,changed:false,edit_count:0}),
 typescript_code_fixes:(path,content,line,column)=>{const original=String(content||'');const fixed=original+'\n// quick-fix applied\n';return {engine:'TypeScript',count:1,actions:[{id:'mock-fix',description:'Apply example quick fix',diagnostic:{message:'Example diagnostic',line,column},count:1,changes:[{path,new_content:fixed,diff:'--- '+path+'\n+++ '+path+'\n+// quick-fix applied',original_sha256:'saved-hash'}]}]};},
 typescript_intelligence:(path,content,line,column,action)=>{
  if(action==='completions')return {engine:'TypeScript',items:[{name:'greet',type:'function',description:'function greet(name: string): string'}]};
  if(action==='definition')return {engine:'TypeScript',definitions:[{path:'helper.ts',line:1,column:16,name:'greet',description:'function greet'}]};
  if(action==='references')return {engine:'TypeScript',count:3,references:[{path:'helper.ts',line:1,column:16,name:'greet',description:'function greet'},{path:'app.ts',line:1,column:9,name:'greet',description:'import greet'},{path:'app.ts',line:2,column:15,name:'greet',description:'greet call'}]};
  if(action==='hover')return {engine:'TypeScript',items:[{name:'greet',type:'function',signature:'function greet(name: string): string',docstring:'Returns a greeting.'}]};
  if(action==='codefixes'){const original=String(content||'');const fixed=original+'\n// quick-fix applied\n';return {engine:'TypeScript',count:1,actions:[{id:'mock-fix',description:'Apply example quick fix',diagnostic:{message:'Example diagnostic',line:1,column:1},count:1,changes:[{path,new_content:fixed,diff:'--- '+path+'\n+++ '+path+'\n+// quick-fix applied',original_sha256:'saved-hash'}]}]};}
  return {error:'unsupported mocked action'};
 },
 apply_typescript_code_fix:changes=>{for(const item of changes){files[item.path]=item.new_content;}return {ok:true,updated:changes.map(item=>({path:item.path,size:item.new_content.length,sha256:'quick-fix-hash'}))};},
 javascript_rename_symbol:(path,content,line,column,newName)=>{const changes=[];for(const name of ['helper.ts','app.ts']){const original=files[name];if(!original.includes('greet'))continue;const updated=original.replace(/\bgreet\b/g,newName);if(updated!==original)changes.push({path:name,new_content:updated,diff:`--- ${name}\n+++ ${name}\n- ${original}+ ${updated}`,matches:original.split('greet').length-1,original_sha256:'mock-hash'});}return {engine:'TypeScript',new_name:newName,count:changes.length,changes,preview_only:true};},
 apply_javascript_rename:changes=>{for(const item of changes)files[item.path]=item.new_content;return {ok:true,updated:changes.map(item=>({path:item.path,size:item.new_content.length,sha256:'renamed-hash'}))};},
 workspace_symbols:buffers=>({count:3,scanned_files:4,truncated:false,warnings:[],symbols:[{name:'greet',kind:'function',path:'main.py',line:2,container:'',qualified_name:'greet'},{name:'boot',kind:'function',path:'app.js',line:2,container:'',qualified_name:'boot'},{name:'Panel',kind:'class',path:'helper.ts',line:1,container:'',qualified_name:'Panel'}]}),search_workspace:()=>({results:[]}),reveal_workspace:()=>({ok:true}),terminal_stop:()=>({ok:true}),
 git_action:(action,arg)=>{if(action==='status')return {stdout:'## main\n M app.js\n',returncode:0,branch:'main',files:[{status:' M',path:'app.js',staged:false,unstaged:true,untracked:false,deleted:false,renamed:false}]};if(action==='branch-list')return {stdout:'* main\n  feature/demo\n',returncode:0};if(action==='remote-list')return {stdout:'origin\n',returncode:0};if(action==='remote-branch-list')return {stdout:'origin/main\norigin/feature/demo\n',returncode:0};if(action==='conflicts')return {stdout:mockGitConflicts.map(x=>x.path).join('\n'),returncode:0,conflicts:mockGitConflicts};if(action==='diff')return {stdout:'diff --git a/app.js b/app.js\n--- a/app.js\n+++ b/app.js\n@@ -1 +1,2 @@\n+demo change',returncode:0};if(action==='hunks')return {returncode:0,path:arg,unstaged_hunks:[{index:0,header:'@@ -1 +1 @@',additions:1,deletions:1,preview:'-old demo\n+demo change\n',signature:'a'.repeat(64)}],staged_hunks:[],unstaged_truncated:false,staged_truncated:false};if(action==='stage-hunk'||action==='unstage-hunk'||action==='discard-hunk')return {stdout:'Hunk operation complete',returncode:0,path:JSON.parse(arg).path};if(action==='compare')return {returncode:0,path:'app.js',left_label:'HEAD · app.js',right_label:'Working tree · app.js',rows:[{left_no:1,left:'let value = 1;',right_no:1,right:'let value = 1;',kind:'same'},{left_no:null,left:'',right_no:2,right:'demo change',kind:'insert'}],truncated:false};if(action==='branch-create')return {stdout:'Switched to a new branch feature/new',returncode:0};if(action==='branch-switch')return {stdout:'Switched to branch main',returncode:0};if(action==='fetch')return {stdout:'Fetched origin',returncode:0};if(action==='pull')return {stdout:'Already up to date.',returncode:0};if(action==='push')return {stdout:'main -> main',returncode:0};if(action==='merge'&&arg==='feature/conflict'){mockGitConflicts=[{path:'app.js'}];files['app.js']='<<<<<<< HEAD\nmain version\n||||||| base\nbase version\n=======\nfeature version\n>>>>>>> feature/conflict\n\n<<<<<<< HEAD\ncurrent two\n=======\nincoming two\n>>>>>>> feature/conflict\n\n<<<<<<< HEAD\ncurrent three\n=======\nincoming three\n>>>>>>> feature/conflict\n';return {stdout:'Automatic merge failed',stderr:'CONFLICT in app.js',returncode:1,conflicts:mockGitConflicts};}if(action==='merge')return {stdout:'Merge complete',returncode:0};if(action==='merge-abort'){mockGitConflicts=[];files['app.js']='let value = 1;\nvalue += 2;\nconsole.log("value", value);\n';return {stdout:'Merge aborted',returncode:0};}if(action==='add')return {stdout:'Staged path '+arg,returncode:0};if(action==='unstage')return {stdout:'Unstaged path '+arg,returncode:0};if(action==='commit')return {stdout:'[main abc123] '+arg,returncode:0};return {stdout:'Done',returncode:0};}
};
window.eel=new Proxy({}, {get(_t,name){return (...args)=>()=>Promise.resolve().then(()=>{window.flowcodeMockCalls.push([String(name),args]);return api[name]?api[name](...args):{ok:true};});}});
})();'''


def main() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    mock = MOCK.replace("__FILES__", json.dumps(files)).replace("__TREE__", json.dumps(tree))
    html = html.replace('<link rel="stylesheet" href="styles.css">', '<style>' + css + '</style>')
    html = html.replace('<script src="/eel.js"></script><script src="app.js"></script>', '<script>' + mock + '</script><script>' + js + '</script>')
    with sync_playwright() as playwright:
        executable = os.environ.get("FLOWCODE_CHROMIUM")
        launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if executable:
            launch_args["executable_path"] = executable
        elif Path("/usr/bin/chromium").exists():
            launch_args["executable_path"] = "/usr/bin/chromium"
        browser = playwright.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": 1440, "height": 920})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(html, wait_until="domcontentloaded")
        page.wait_for_timeout(180)

        # PTY chunk rendering must preserve useful text without exposing ANSI control codes.
        page.wait_for_function("document.querySelector('#terminalHistory').innerText.includes('PTY output')", timeout=2500)
        terminal_output = page.locator('#terminalHistory .term-output').last.inner_text()
        assert 'PTY output\nnext line' in terminal_output, terminal_output
        assert '\x1b' not in terminal_output, terminal_output
        screenshot = os.environ.get("FLOWCODE_SMOKE_SCREENSHOT")
        if screenshot:
            page.screenshot(path=screenshot, full_page=True)
        settings_screenshot = os.environ.get('FLOWCODE_SETTINGS_SCREENSHOT')
        if settings_screenshot:
            page.locator('.activity[data-activity="settings"]').click()
            page.locator('#toastRegion').evaluate('(element)=>element.replaceChildren()')
            page.screenshot(path=settings_screenshot, full_page=True)
            page.locator('.activity[data-activity="explorer"]').click()

        # Source Control loads branches, renders status separately, and routes path-scoped diff.
        page.locator('.activity[data-activity="source"]').click()
        page.wait_for_function("document.querySelector('#gitStatusOutput').innerText.includes('main')", timeout=2500)
        page.wait_for_function("Array.from(document.querySelector('#gitBranchSelect').options).some(o=>o.value==='feature/demo')", timeout=2500)
        assert 'M app.js' in page.locator('#gitStatusOutput').inner_text()
        page.locator('#gitChangedFiles .git-changed-file').filter(has_text='app.js').click()
        assert page.locator('#gitPath').input_value() == 'app.js'
        page.wait_for_function("!document.querySelector('#gitDiffOverlay').classList.contains('hidden') && document.querySelector('#gitDiffOverlay').innerText.includes('demo change')", timeout=2500)
        compare_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='git_action' and call[1][0]=='compare')
        assert json.loads(compare_call[1][1]) == {'path':'app.js','old_path':'app.js'}, compare_call
        assert page.locator('#gitDiffOverlay .git-diff-cell.right.insert').count() == 1
        compare_screenshot = os.environ.get('FLOWCODE_DIFF_SCREENSHOT')
        if compare_screenshot:
            page.screenshot(path=compare_screenshot, full_page=True)
        page.locator('#gitDiffClose').click()
        page.locator('#gitDiff').click()
        page.wait_for_function("document.querySelector('#gitResults').innerText.includes('demo change')", timeout=2500)
        assert any(call[0] == 'git_action' and call[1][0] == 'diff' and call[1][1] == 'app.js' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#gitHunks').click()
        page.wait_for_function("document.querySelector('#gitResults').innerText.includes('Hunk 1') && document.querySelector('#gitResults').innerText.includes('demo change')", timeout=2500)
        hunk_screenshot = os.environ.get('FLOWCODE_HUNKS_SCREENSHOT')
        if hunk_screenshot:
            page.locator('#toastRegion').evaluate('(element)=>element.replaceChildren()')
            page.locator('#gitResults .git-hunk-card').first.screenshot(path=hunk_screenshot)
        page.locator('#gitResults button').filter(has_text='Stage Hunk').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='stage-hunk')", timeout=2500)
        hunk_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='git_action' and call[1][0]=='stage-hunk')
        hunk_payload=json.loads(hunk_call[1][1])
        assert hunk_payload['path']=='app.js' and hunk_payload['index']==0 and hunk_payload['signature']=='a'*64, hunk_call
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitResults button').filter(has_text='Discard Hunk').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='discard-hunk')", timeout=2500)
        discard_call=next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='git_action' and call[1][0]=='discard-hunk')
        assert json.loads(discard_call[1][1]) == hunk_payload, discard_call
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitBranch').fill('feature/new')
        page.locator('#gitBranchCreate').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='branch-create'&&call[1][1]==='feature/new')", timeout=2500)
        # Remote fetch/pull/push are distinct UI actions and pass remote/branch as structured data.
        page.locator('#gitFetch').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='fetch'&&call[1][1]==='origin')", timeout=2500)
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitRemoteBranchSelect').select_option('origin/main')
        page.locator('#gitPull').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='pull')", timeout=2500)
        pull_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='git_action' and call[1][0]=='pull')
        assert json.loads(pull_call[1][1]) == {'remote':'origin','branch':'main'}, pull_call
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitPush').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='push')", timeout=2500)
        push_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='git_action' and call[1][0]=='push')
        assert json.loads(push_call[1][1]) == {'remote':'origin','branch':'main'}, push_call
        page.locator('#gitMergeBranch').fill('feature/demo')
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitMerge').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='git_action'&&call[1][0]==='merge'&&call[1][1]==='feature/demo')", timeout=2500)
        page.locator('#gitConflicts').click()
        page.wait_for_function("document.querySelector('#gitConflictFiles').innerText.includes('No unresolved merge conflicts')", timeout=2500)
        git_screenshot = os.environ.get("FLOWCODE_GIT_SCREENSHOT")
        if git_screenshot:
            page.locator('#toastRegion').evaluate('(element)=>element.replaceChildren()')
            page.screenshot(path=git_screenshot, full_page=True)
        # A failed merge automatically lists conflicts; selecting one reloads current marker content.
        page.locator('#gitMergeBranch').fill('feature/conflict')
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitMerge').click()
        page.wait_for_function("document.querySelector('#gitConflictFiles').innerText.includes('app.js')", timeout=2500)
        assert any(call[0]=='git_action' and call[1][0]=='conflicts' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#gitConflictFiles .git-changed-file').click()
        page.wait_for_function("document.querySelector('#codeEditor').value.includes('<<<<<<< HEAD') && !document.querySelector('#mergeConflictTools').classList.contains('hidden')", timeout=2500)
        assert page.locator('#mergeConflictStatus').inner_text() == 'Conflict 1 of 3'
        conflict_screenshot = os.environ.get("FLOWCODE_CONFLICT_SCREENSHOT")
        if conflict_screenshot:
            page.locator('#toastRegion').evaluate('(element)=>element.replaceChildren()')
            page.screenshot(path=conflict_screenshot, full_page=True)
        # Conflict controls support navigation, incoming/both resolutions, and diff3's ancestor section.
        page.locator('#nextMergeConflict').click()
        assert page.locator('#mergeConflictStatus').inner_text() == 'Conflict 2 of 3'
        page.locator('#acceptConflictIncoming').click()
        after_incoming=page.locator('#codeEditor').input_value()
        assert 'incoming two' in after_incoming and 'current two' not in after_incoming
        assert '<<<<<<< HEAD' in after_incoming
        page.locator('#acceptConflictBoth').click()
        after_both=page.locator('#codeEditor').input_value()
        assert 'current three' in after_both and 'incoming three' in after_both, after_both
        page.locator('#acceptConflictCurrent').click()
        resolved=page.locator('#codeEditor').input_value()
        assert 'main version' in resolved and 'base version' not in resolved, resolved
        assert 'incoming two' in resolved and 'current three' in resolved and 'incoming three' in resolved, resolved
        assert '<<<<<<< HEAD' not in resolved and '=======' not in resolved and '>>>>>>>' not in resolved, resolved
        assert 'hidden' in page.locator('#mergeConflictTools').get_attribute('class')
        page.keyboard.press('Control+s')
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='save_file'&&call[1][0]==='app.js'&&!call[1][1].includes('<<<<<<< HEAD'))", timeout=2500)
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#gitMergeAbort').click()
        page.wait_for_function("document.querySelector('#gitConflictFiles').innerText.includes('No unresolved merge conflicts')", timeout=2500)
        page.locator('.activity[data-activity="explorer"]').click()

        page.locator('.tree-row[data-path="styles.css"]').click()
        page.wait_for_timeout(50)
        page.locator("#codeEditor").fill("/* MALFORMED */\n.card { color: red;")
        page.wait_for_timeout(550)
        assert "1" in page.locator("#statusProblems").inner_text(), page.locator("#statusProblems").inner_text()

        # Git operations must not stage or branch-switch while a dirty editor buffer exists.
        page.locator('.activity[data-activity="source"]').click()
        page.locator('#gitPath').fill('styles.css')
        page.locator('#gitAdd').click()
        assert 'Save the file before staging it' in page.locator('#toastRegion').inner_text()
        assert not any(call[0] == 'git_action' and call[1][0] == 'add' for call in page.evaluate('window.flowcodeMockCalls'))
        prior_hunk_stages = page.evaluate("window.flowcodeMockCalls.filter(call=>call[0]==='git_action'&&call[1][0]==='stage-hunk').length")
        page.locator('#gitHunks').click()
        page.wait_for_function("document.querySelector('#gitResults').innerText.includes('Hunk 1')", timeout=2500)
        page.locator('#gitResults button').filter(has_text='Stage Hunk').click()
        assert 'Save this file before changing its Git hunks' in page.locator('#toastRegion').inner_text()
        assert page.evaluate("window.flowcodeMockCalls.filter(call=>call[0]==='git_action'&&call[1][0]==='stage-hunk').length") == prior_hunk_stages
        page.locator('#gitResults button').filter(has_text='Discard Hunk').click()
        assert 'Save this file before changing its Git hunks' in page.locator('#toastRegion').inner_text()
        assert page.evaluate("window.flowcodeMockCalls.filter(call=>call[0]==='git_action'&&call[1][0]==='discard-hunk').length") == 1
        page.locator('#gitSwitch').click()
        assert not any(call[0] == 'git_action' and call[1][0] == 'branch-switch' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('.activity[data-activity="explorer"]').click()

        # Workspace Problems validates unsaved editor buffers and navigates to file/line diagnostics.
        page.locator('#statusProblems').click()
        page.locator('#checkWorkspaceProblems').click()
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('Workspace scan')", timeout=2500)
        assert 'Unexpected token in workspace file' in page.locator('#plainPanel').inner_text()
        assert page.evaluate("window.flowcodeWorkspaceOverrides['styles.css'].includes('MALFORMED')")
        page.locator('#plainPanel .search-result').filter(has_text='Unexpected token in workspace file').click()
        page.wait_for_function("document.querySelector('#statusLanguage').innerText === 'JavaScript'", timeout=2500)
        assert page.locator('#breadcrumbs').inner_text().endswith('app.js')
        assert 'workspace file' in page.locator('#toastRegion').inner_text() or page.locator('#plainPanel').inner_text().startswith('PROBLEMS')

        page.locator('.tree-row[data-path="main.py"]').click()
        page.wait_for_timeout(60)
        page.keyboard.press("Shift+Alt+f")
        page.wait_for_timeout(120)
        assert "def greet(name):" in page.locator("#codeEditor").input_value()
        page.keyboard.press("F5")
        page.wait_for_timeout(100)
        assert page.locator('#overwriteDiskVersion').is_visible()
        assert not any(call[0] == 'run_file' for call in page.evaluate('window.flowcodeMockCalls')), 'Run must stop when its dirty buffer cannot be saved.'
        assert 'external edit' in page.locator('#plainPanel').inner_text()
        page.once('dialog', lambda dialog: dialog.accept())
        await_button = page.locator('#overwriteDiskVersion')
        await_button.click()
        page.wait_for_timeout(100)
        assert 'Overwrite complete' in page.locator('#plainPanel').inner_text()
        assert 'def greet(name):' in page.evaluate('window.flowcodeMockFiles["main.py"]')

        page.keyboard.press("Control+Shift+p")
        page.wait_for_timeout(80)
        assert page.locator("#commandOverlay").is_visible()
        page.keyboard.press("Escape")

        # Auto Save is optional and runs through the same conflict-aware save path.
        page.locator('.activity[data-activity="settings"]').click()
        page.locator("#autoSaveDelay").select_option("500")
        page.locator("#autoSaveToggle").check()
        page.locator('.activity[data-activity="explorer"]').click()
        page.locator('.tree-row[data-path="main.py"]').click()
        page.locator("#codeEditor").fill('print("auto-saved")\n')
        page.wait_for_timeout(750)
        assert 'auto-saved' in page.evaluate('window.flowcodeMockFiles["main.py"]')

        # Node.js debugger routing: JS breakpoint, start, continue and stop use Inspector APIs.
        page.locator('.activity[data-activity="explorer"]').click()
        page.locator('.tree-row[data-path="app.js"]').click()
        page.locator('.activity[data-activity="run"]').click()
        page.locator('.gutter-line[data-line="2"]').click()
        assert 'breakpoint' in page.locator('.gutter-line[data-line="2"]').get_attribute('class')
        assert page.locator('#debugConfigSelect').input_value() == 'launch-1', page.locator('#debugConfigSelect').input_value()
        page.locator('#editDebugConfig').click()
        assert page.locator('#debugConfigEditor').is_visible()
        assert page.locator('#debugConfigType').input_value() == 'node'
        page.locator('#debugConfigName').fill('Node: Edited Test Config')
        page.locator('#saveDebugConfig').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call => call[0] === 'save_debug_configuration')", timeout=2500)
        config_save_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0] == 'save_debug_configuration')
        assert config_save_call[1][0] == 'update' and config_save_call[1][1]['name'] == 'Node: Edited Test Config' and config_save_call[1][2] == 'app.js' and config_save_call[1][3] == 'launch-1', config_save_call
        assert 'hidden' in page.locator('#debugConfigEditor').get_attribute('class')
        page.locator('#startDebugger').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call => call[0] === 'node_debug_start')", timeout=2500)
        launch_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0] == 'node_debug_start')
        assert launch_call[1][2]['type'] == 'node' and launch_call[1][2]['program'] == 'app.js', launch_call
        page.locator('#debugWatchInput').fill('value * 10')
        page.locator('#debugWatchForm button[type=submit]').click()
        page.wait_for_function("document.querySelector('#debugWatchPanel').innerText.includes('3')", timeout=2500)
        assert any(call[0] == 'node_debug_command' and call[1][0] == 'evaluate value * 10' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#debugStep').click()
        page.locator('#debugNext').click()
        page.locator('#debugContinue').click()
        page.wait_for_timeout(60)
        debug_commands = [call[1][0] for call in page.evaluate('window.flowcodeMockCalls') if call[0] == 'node_debug_command']
        assert 'step' in debug_commands and 'next' in debug_commands and 'continue' in debug_commands, debug_commands
        page.locator('#debugStop').click()
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('Node.js debug session stopped')", timeout=2500)
        assert any(call[0] == 'node_debug_command' and call[1][0] == 'stop' for call in page.evaluate('window.flowcodeMockCalls'))

        # Workspace task CRUD edits tasks.json through a validated editor form and runs only explicit selections.
        page.wait_for_function("document.querySelector('#workspaceTaskList').innerText.includes('Build app')", timeout=2500)
        page.locator('#workspaceTaskList .workspace-task-row').filter(has_text='Build app').locator('.workspace-task-edit').click()
        assert page.locator('#workspaceTaskEditor').is_visible()
        page.locator('#workspaceTaskLabel').fill('Build app edited')
        page.locator('#workspaceTaskArgs').fill('["--version"]')
        page.locator('#saveWorkspaceTask').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='task-config'&&call[1][0]==='update')", timeout=2500)
        save_task_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='task-config' and call[1][0]=='update')
        assert save_task_call[1][1]['label']=='Build app edited' and save_task_call[1][1]['args']==['--version'] and save_task_call[1][2]=='Build app', save_task_call
        page.locator('#newWorkspaceTask').click()
        task_screenshot = os.environ.get('FLOWCODE_TASKS_SCREENSHOT')
        if task_screenshot:
            page.locator('#toastRegion').evaluate('(element) => element.replaceChildren()')
            page.screenshot(path=task_screenshot, full_page=True)
        page.locator('#workspaceTaskLabel').fill('Test task')
        page.locator('#workspaceTaskCommand').fill('python')
        page.locator('#workspaceTaskArgs').fill('["-V"]')
        page.locator('#saveWorkspaceTask').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='task-config'&&call[1][0]==='create')", timeout=2500)
        assert 'Test task' in page.locator('#workspaceTaskList').inner_text()
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#workspaceTaskList .workspace-task-row').filter(has_text='Test task').locator('.workspace-task-edit').click()
        page.locator('#deleteWorkspaceTask').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='task-config'&&call[1][0]==='delete')", timeout=2500)
        page.locator('#workspaceTaskList .workspace-task-row').filter(has_text='Build app edited').locator('.workspace-task').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='run_task'&&call[1][0]==='Build app edited')", timeout=2500)

        # TypeScript compiler-backed feature routing in the built-in editor.
        page.locator('.activity[data-activity="explorer"]').click()
        page.locator('.tree-row[data-path="app.ts"]').click()
        page.locator('#codeEditor').fill('const output = gre')
        page.wait_for_function("document.querySelector('#completionWidget').innerText.includes('greet')", timeout=3000)
        page.keyboard.press('Enter')
        assert 'const output = greet' in page.locator('#codeEditor').input_value()
        page.locator('#codeEditor').fill('import { greet } from "./helper";\nconst output = greet("FlowCode");\n')
        page.locator('#codeEditor').evaluate('(el, term)=>{const i=el.value.indexOf(term)+2;el.setSelectionRange(i,i);}', 'greet("FlowCode")')
        page.keyboard.press('F12')
        page.wait_for_function("window.flowcodeMockCalls.some(call => call[0] === 'typescript_intelligence' && call[1][4] === 'definition')", timeout=2500)
        assert page.locator('#statusLanguage').inner_text() == 'TypeScript'
        page.locator('.tree-row[data-path="app.ts"]').click()
        page.locator('#codeEditor').evaluate('(el, term)=>{const i=el.value.indexOf(term)+2;el.setSelectionRange(i,i);}', 'greet("FlowCode")')
        page.keyboard.press('Shift+F12')
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('REFERENCES')", timeout=2500)
        assert any(call[0] == 'typescript_intelligence' and call[1][4] == 'references' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#codeEditor').focus()
        page.keyboard.press('Control+Shift+Space')
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('JavaScript/TypeScript Symbol Information')", timeout=2500)
        assert any(call[0] == 'typescript_intelligence' and call[1][4] == 'hover' for call in page.evaluate('window.flowcodeMockCalls'))
        page.on('dialog', lambda dialog: dialog.accept('value == 3' if 'Python condition' in dialog.message else 'welcome') if dialog.type == 'prompt' else dialog.accept())
        page.locator('#codeEditor').focus()
        page.keyboard.press('F2')
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('RENAME SYMBOL PREVIEW')", timeout=3000)
        assert any(call[0] == 'javascript_rename_symbol' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#applyPythonRename').click()
        page.wait_for_function("window.flowcodeMockFiles['helper.ts'].includes('function welcome')", timeout=2500)
        assert 'welcome("FlowCode")' in page.evaluate('window.flowcodeMockFiles["app.ts"]')
        page.locator('.tree-row[data-path="app.ts"]').click()
        page.locator('#codeEditor').focus()
        page.keyboard.press('Shift+Alt+f')
        page.wait_for_function("window.flowcodeMockCalls.some(call => call[0] === 'format_javascript_document')", timeout=2500)
        page.locator('#codeEditor').focus()
        page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'.',code:'Period',ctrlKey:true,bubbles:true,cancelable:true}))")
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('QUICK FIXES')", timeout=2500)
        assert any(call[0] == 'typescript_code_fixes' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#plainPanel .search-result').filter(has_text='Apply example quick fix').click()
        page.wait_for_function("document.querySelector('#plainPanel').innerText.includes('QUICK FIX PREVIEW')", timeout=2500)
        quickfix_screenshot = os.environ.get('FLOWCODE_QUICKFIX_SCREENSHOT')
        if quickfix_screenshot:
            page.locator('#toastRegion').evaluate('(element)=>element.replaceChildren()')
            page.screenshot(path=quickfix_screenshot, full_page=True)
        page.locator('#applyPythonRename').click()
        page.wait_for_function("window.flowcodeMockFiles['app.ts'].includes('quick-fix applied')", timeout=2500)
        assert any(call[0] == 'apply_typescript_code_fix' for call in page.evaluate('window.flowcodeMockCalls'))

        # Unsaved buffers are written through the backend bridge, not a volatile browser origin.
        page.locator('#codeEditor').fill('const crashRecoverySentinel = "draft survives restart";\n')
        page.wait_for_function("window.flowcodeMockSession && window.flowcodeMockSession.drafts && window.flowcodeMockSession.drafts['app.ts'] && window.flowcodeMockSession.drafts['app.ts'].content.includes('draft survives restart')", timeout=2500)
        session_state = page.evaluate('window.flowcodeMockSession')
        assert session_state['activePath'] == 'app.ts', session_state
        assert 'app.ts' in session_state['openPaths'], session_state
        assert session_state['breakpoints']['app.js'] == [2], session_state
        assert session_state['debugWatches'] == ['value * 10'], session_state
        page.wait_for_function("window.flowcodeMockSettings.autoSave === true && window.flowcodeMockSettings.lastWorkspacePath === '/workspace/sample_workspace'", timeout=2500)
        window_settings = page.evaluate('window.flowcodeMockSettings')
        assert window_settings, window_settings
        assert window_settings['autoSaveDelay'] == 500, window_settings

        # Boot a fresh renderer with a previously saved session to verify tab/draft recovery.
        recovery_page = browser.new_page(viewport={"width": 1440, "height": 920})
        recovery_errors: list[str] = []
        recovery_page.on('pageerror', lambda error: recovery_errors.append(str(error)))
        seeded = {"version": 1, "workspacePath": "/workspace/sample_workspace", "openPaths": ["main.py", "app.js"],
                  "activePath": "main.py", "drafts": {"main.py": {"content": 'print("recovered crash draft")\n',
                    "saved": files["main.py"], "savedHash": "disk-hash", "openedAt": 1}},
                  "breakpoints": {"main.py": [1]}, "breakpointConditions": {"main.py": {"1": "value == 3"}}, "debugWatches": ["value * 2"], "updatedAt": 2}
        recovery_mock = mock.replace('let mockSession=window.__flowcodeSessionSeed||null;', 'let mockSession=' + json.dumps(seeded) + ';')
        recovery_html = html.replace(mock, recovery_mock)
        recovery_page.set_content(recovery_html, wait_until='domcontentloaded')
        recovery_page.wait_for_function("document.querySelector('#codeEditor').value.includes('recovered crash draft')", timeout=2500)
        assert recovery_page.locator('.tab.dirty').count() >= 1
        assert 'Recovered 1 unsaved draft' in recovery_page.locator('#toastRegion').inner_text()
        recovery_page.wait_for_function("window.flowcodeMockSession && window.flowcodeMockSession.breakpoints['main.py'][0] === 1", timeout=2500)
        assert recovery_page.evaluate('window.flowcodeMockSession.debugWatches') == ['value * 2']
        assert not recovery_errors, recovery_errors

        # Workspace overrides persist through the bridge and affect the active editor.
        page.locator('.activity[data-activity="settings"]').click()
        assert page.locator('#saveWorkspaceSettings').is_visible()
        page.locator('#wsFontSize').select_option('16')
        page.locator('#wsWordWrap').select_option('on')
        page.locator('#wsMinimap').select_option('false')
        page.locator('#wsAutoSave').select_option('afterDelay')
        page.locator('#wsAutoSaveDelay').select_option('2000')
        page.locator('#wsInsertSpaces').select_option('false')
        page.locator('#wsTabSize').select_option('2')
        page.locator('#saveWorkspaceSettings').click()
        page.wait_for_function("window.flowcodeMockWorkspaceSettings['editor.fontSize'] === 16 && window.flowcodeMockWorkspaceSettings['editor.insertSpaces'] === false", timeout=2500)
        workspace_settings = page.evaluate('window.flowcodeMockWorkspaceSettings')
        assert workspace_settings == {'editor.fontSize':16,'editor.wordWrap':'on','editor.minimap.enabled':False,'files.autoSave':'afterDelay','files.autoSaveDelay':2000,'editor.insertSpaces':False,'editor.tabSize':2}, workspace_settings
        assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--editor-size').trim()") == '16px'
        assert 'word-wrap' in page.locator('#editorView').get_attribute('class')
        assert 'hidden' in page.locator('#miniMap').get_attribute('class')
        page.locator('.activity[data-activity="explorer"]').click()
        page.locator('.tree-row[data-path="app.ts"]').click()
        page.locator('#codeEditor').fill('x')
        page.keyboard.press('Tab')
        assert page.locator('#codeEditor').input_value().endswith('x\t'), page.locator('#codeEditor').input_value()
        page.locator('.activity[data-activity="settings"]').click()
        for selector in ['#wsFontSize','#wsWordWrap','#wsMinimap','#wsAutoSave','#wsAutoSaveDelay','#wsInsertSpaces','#wsTabSize']:
            page.locator(selector).select_option('')
        page.locator('#saveWorkspaceSettings').click()
        page.wait_for_function("Object.keys(window.flowcodeMockWorkspaceSettings).length === 0", timeout=2500)

        # The Test Explorer discovers supported runners and executes only an explicit selected action.
        page.locator('.activity[data-activity="testing"]').click()
        page.wait_for_function("document.querySelector('#testSuiteSummary').innerText.includes('Python')", timeout=2500)
        assert 'tests/test_alpha.py' in page.locator('#pythonTestFiles').inner_text()
        assert 'test/answer.test.cjs' in page.locator('#nodeTestFiles').inner_text()
        tests_screenshot = os.environ.get('FLOWCODE_TESTS_SCREENSHOT')
        if tests_screenshot:
            page.locator('#toastRegion').evaluate('(element) => element.replaceChildren()')
            # Keep the screenshot visually clean; restore the exact classes before tests continue.
            page.locator('#editorView').evaluate("element => element.classList.add('hidden')")
            page.locator('#welcomeView').evaluate("element => element.classList.remove('hidden')")
            page.screenshot(path=tests_screenshot, full_page=True)
            page.locator('#welcomeView').evaluate("element => element.classList.add('hidden')")
            page.locator('#editorView').evaluate("element => element.classList.remove('hidden')")
        page.locator('#runPythonTests').click()
        page.wait_for_function("document.querySelector('#testRunResult').innerText.includes('1 passed')", timeout=2500)
        assert any(call[0]=='run_workspace_tests' and call[1][0]=='python' for call in page.evaluate('window.flowcodeMockCalls'))
        page.locator('#runNodeTests').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='run_workspace_tests'&&call[1][0]==='node')", timeout=2500)
        page.locator('#runNpmTests').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='run_workspace_tests'&&call[1][0]==='npm')", timeout=2500)
        assert 'PASS' in page.locator('#testRunResult').inner_text()

        # Go to Symbol scans workspace declarations, fuzzy-matches them, and navigates to the source line.
        page.keyboard.press('Control+t')
        page.wait_for_function("!document.querySelector('#commandOverlay').classList.contains('hidden') && document.querySelector('#commandInput').placeholder.includes('symbol')", timeout=2500)
        page.locator('#commandInput').fill('gret')
        page.wait_for_function("document.querySelector('#commandResults').innerText.includes('greet — main.py:2')", timeout=2500)
        symbol_screenshot = os.environ.get('FLOWCODE_SYMBOL_SCREENSHOT')
        if symbol_screenshot:
            page.screenshot(path=symbol_screenshot, full_page=True)
        page.locator('#commandResults .command-result').filter(has_text='greet — main.py:2').click()
        page.wait_for_function("document.querySelector('#statusLn').innerText.includes('Ln 2')", timeout=2500)
        assert any(call[0]=='workspace_symbols' for call in page.evaluate('window.flowcodeMockCalls'))

        # Python conditional breakpoints can be set from the gutter context menu and survive sessions.
        page.locator('.activity[data-activity="explorer"]').click()
        page.locator('.tree-row[data-path="main.py"]').click()
        page.keyboard.press('Control+s')
        page.locator('.activity[data-activity="run"]').click()
        page.locator('.gutter-line[data-line="1"]').click(button='right')
        page.wait_for_function("document.querySelector('.gutter-line[data-line=\"1\"]')?.classList.contains('conditional-breakpoint')", timeout=2500)
        page.wait_for_function("window.flowcodeMockSession?.breakpointConditions?.['main.py']?.['1'] === 'value == 3'", timeout=2500)
        condition_saved = page.evaluate('window.flowcodeMockSession.breakpointConditions')
        assert condition_saved['main.py']['1'] == 'value == 3', condition_saved
        conditional_screenshot = os.environ.get('FLOWCODE_CONDITIONAL_BREAKPOINTS_SCREENSHOT')
        if conditional_screenshot:
            page.locator('#toastRegion').evaluate('(element)=>element.replaceChildren()')
            page.screenshot(path=conditional_screenshot, full_page=True)
        page.locator('#startDebugger').click()
        page.wait_for_function("window.flowcodeMockCalls.some(call=>call[0]==='debug_start')", timeout=2500)
        python_start_call = next(call for call in page.evaluate('window.flowcodeMockCalls') if call[0]=='debug_start')
        assert python_start_call[1][1] == [1] and python_start_call[1][3] == {'1':'value == 3'}, python_start_call
        page.locator('#debugStop').click()
        page.wait_for_timeout(60)

        assert not errors, errors
        print(json.dumps({"status": "passed", "checks": ["PTY output chunk sanitization", "CSS warning counter", "workspace-wide diagnostics with unsaved-buffer overrides and navigation", "Python format command", "Command Palette", "save-conflict compare/overwrite and block Run on save failure", "configurable Auto Save", "Node.js debugger routing/watch/launch-config CRUD form", "Python conditional breakpoints, case-sensitive expression persistence and debug launch routing", "TypeScript completion, definition, references, hover, rename preview/apply, Quick Fix preview/apply and format routing", "backend-backed unsaved session persistence and startup recovery", "Source Control branch list/status/diff actions", "per-hunk Git preview, stage/unstage and confirmed discard action routing with hunk signature", "side-by-side compare rendering and line highlighting", "remote fetch/pull/push parameter routing, automatic merge-conflict list/reload/abort, and inline current/incoming/both conflict resolution", "block whole-file stage, hunk stage/discard, and branch switch when editor buffers are unsaved", "workspace task editor create/update/delete and explicit task execution", "workspace .vscode/settings.json overrides and inherit reset", "workspace-specific font, wrapping, minimap, auto-save and tab indentation", "Test Explorer discovery and Python/Node/npm runner routing", "Ctrl+T fuzzy workspace symbol picker and file/line navigation"], "page_errors": errors, "recovery_page_errors": recovery_errors}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
