const STYLE = `
.team-code-page{height:calc(100vh - 66px);min-height:560px;display:grid;grid-template-columns:265px minmax(0,1fr);gap:12px;color:inherit}.team-code-page *{box-sizing:border-box}.team-code-sidebar,.team-code-workspace{min-height:0;border:1px solid var(--stroke);border-radius:8px;background:var(--card-bg)}.team-code-sidebar{display:flex;flex-direction:column;padding:12px}.team-code-heading{margin:0 0 9px;font-size:.86rem;font-weight:600}.team-code-list{display:flex;min-height:0;flex:1;flex-direction:column;gap:3px;overflow:auto}.team-code-list button{padding:8px 9px;border:0;border-radius:5px;background:transparent;color:inherit;text-align:left;cursor:pointer;font-size:.72rem}.team-code-list button:hover{background:var(--card-hover)}.team-code-list button.active{background:var(--selected);color:var(--accent)}.team-code-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}.team-code-page button,.team-code-page select,.team-code-page input{min-height:31px;border:1px solid var(--stroke);border-radius:5px;background:var(--card-bg);color:inherit;font-size:.7rem}.team-code-page button{padding:4px 10px;cursor:pointer}.team-code-page button:hover:not(:disabled){background:var(--card-hover)}.team-code-page button.primary{border-color:var(--accent);background:var(--accent);color:#102a35}.team-code-page button.danger{color:#ef8b8b}.team-code-page button:disabled{opacity:.48;cursor:default}.team-code-workspace{display:grid;grid-template-rows:auto minmax(0,1fr) auto;padding:10px}.team-code-toolbar,.team-code-footer{display:flex;align-items:center;gap:8px}.team-code-toolbar{min-height:54px;padding-bottom:8px}.team-code-portrait{display:grid;width:52px;height:52px;place-items:center}.team-code-portrait img{max-width:48px;max-height:48px;object-fit:contain}.team-code-member{min-width:190px;padding:0 8px}.team-code-spacer{flex:1}.team-code-editor{min-height:0;overflow:hidden;border:1px solid var(--stroke);border-radius:6px;background:rgba(0,0,0,.16)}.team-code-editor textarea{width:100%;height:100%;resize:none;border:0;outline:0;padding:10px 12px;background:transparent;color:inherit;font:13px/1.55 Consolas,'Cascadia Mono',monospace;tab-size:4;white-space:pre;overflow:auto}.team-code-footer{min-height:44px;padding-top:8px}.team-code-status{min-width:0;flex:1;color:var(--text-muted);font-size:.68rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.team-code-status.dirty{color:#f5c36b}.team-code-empty{display:grid;height:100%;place-items:center;color:var(--text-muted)}.team-code-dialog{position:fixed;inset:0;z-index:1000;display:grid;place-items:center;background:rgba(0,0,0,.52)}.team-code-dialog-card{width:min(520px,calc(100vw - 32px));padding:18px;border:1px solid var(--stroke);border-radius:10px;background:var(--card-bg);box-shadow:0 16px 48px rgba(0,0,0,.35)}.team-code-dialog-card h2{margin:0 0 14px;font-size:1rem}.team-code-fields{display:grid;gap:9px}.team-code-fields label{display:grid;gap:4px;font-size:.7rem}.team-code-fields input,.team-code-fields select{width:100%;padding:0 8px}.team-code-dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}@media(max-width:760px){.team-code-page{height:auto;grid-template-columns:1fr}.team-code-sidebar{max-height:260px}.team-code-workspace{min-height:620px}}
`;

function html(value) {
  return String(value ?? "").replace(/[&<>\"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;"})[char]);
}

export function mount(container, context) {
  const state = {characters:[],teams:[],team:null,member:null,code:"",cleanCode:"",busy:false,destroyed:false};
  const dirty = () => Boolean(state.member && state.code !== state.cleanCode);
  const setDirty = () => context.setDirty(dirty());
  const confirmDiscard = () => !dirty() || window.confirm(context.t("Discard unsaved character code changes?"));

  async function refresh(preferredKey) {
    const data = await context.query("state");
    state.characters = data.characters || [];
    state.teams = data.teams || [];
    const selected = state.teams.find((team) => team.key === preferredKey)
      || state.teams.find((team) => team.key === state.team?.key)
      || state.teams[0] || null;
    state.team = selected;
    if (selected) await loadMember(selected.members.includes(state.member?.class_name) ? state.member.class_name : selected.members[0], false);
    else { state.member = null; state.code = ""; state.cleanCode = ""; setDirty(); }
  }

  async function loadTeam(key) {
    if (!confirmDiscard()) return;
    const selected = state.teams.find((team) => team.key === key);
    if (!selected) return;
    state.team = selected;
    await loadMember(selected.members[0], false);
  }

  async function loadMember(className, guard=true) {
    if (!state.team || (guard && !confirmDiscard())) { render(); return; }
    state.busy = true; render();
    try {
      const result = await context.query("member", {team:state.team.members,class_name:className});
      state.member = result; state.code = result.code; state.cleanCode = result.code; setDirty();
    } catch (error) { context.notify(error.message || String(error), "error"); }
    finally { state.busy = false; render(); }
  }

  async function save() {
    if (!state.team || !state.member) return false;
    state.busy = true; render();
    try {
      const result = await context.action("save-member", {team:state.team.members,class_name:state.member.class_name,code:state.code});
      state.member = result; state.code = result.code; state.cleanCode = result.code; setDirty();
      context.notify(context.t(result.message), "success"); return true;
    } catch (error) { context.notify(error.message || String(error), "error"); return false; }
    finally { state.busy = false; render(); }
  }

  async function reset() {
    if (!state.team || !state.member || !window.confirm(context.t("Reset this character to built in code for this team?"))) return;
    state.busy = true; render();
    try {
      const result = await context.action("reset-member", {team:state.team.members,class_name:state.member.class_name});
      state.member = result; state.code = result.code; state.cleanCode = result.code; setDirty();
      context.notify(context.t(result.message), "success");
    } catch (error) { context.notify(error.message || String(error), "error"); }
    finally { state.busy = false; render(); }
  }

  function dialog(title, fields, onSubmit) {
    const overlay = document.createElement("div"); overlay.className = "team-code-dialog";
    overlay.innerHTML = `<div class="team-code-dialog-card"><h2>${html(context.t(title))}</h2><form><div class="team-code-fields">${fields}</div><div class="team-code-dialog-actions"><button type="button" data-cancel>${html(context.t("Cancel"))}</button><button class="primary" type="submit">${html(context.t(title))}</button></div></form></div>`;
    const close = () => overlay.remove(); overlay.querySelector("[data-cancel]").onclick = close;
    overlay.addEventListener("click", (event) => {if(event.target === overlay) close();});
    overlay.querySelector("form").onsubmit = async (event) => {event.preventDefault(); try {await onSubmit(new FormData(event.currentTarget)); close();} catch(error){context.notify(error.message || String(error),"error");}};
    document.body.appendChild(overlay);
  }

  function createTeam() {
    if (!confirmDiscard()) return;
    const options = state.characters.map((item) => `<option value="${html(item.class_name)}">${html(context.t(item.display_name))}</option>`).join("");
    dialog("Create Team", [0,1,2].map((index) => `<label>${html(context.t(`Character ${index+1}`))}<select name="member">${options}</select></label>`).join(""), async (form) => {
      const members = form.getAll("member");
      const result = await context.action("create-team", {members}); await refresh(result.key); render(); context.notify(context.t(result.message),"success");
    });
  }

  async function deleteTeam() {
    if (!state.team || !confirmDiscard() || !window.confirm(context.t("Permanently delete the team {team}?", {team:state.team.display_name}))) return;
    state.busy=true; render();
    try {const result=await context.action("delete-team",{team:state.team.members}); state.team=null; state.member=null; await refresh(); context.notify(context.t(result.message),"success");}
    catch(error){context.notify(error.message||String(error),"error");}
    finally{state.busy=false;render();}
  }

  function exportTeam() {
    if (!state.team) return;
    if (dirty()) {context.notify(context.t("Save changes before exporting."),"error"); return;}
    const defaultName = state.team.members.map((name) => state.characters.find((item) => item.class_name === name)?.display_name || name).join("_").replaceAll(" ","_");
    dialog("Export Team", `<label>${html(context.t("Name"))}<input name="name" required value="${html(defaultName)}"></label><label>${html(context.t("Description"))}<input name="description" required></label><label>${html(context.t("Author"))}<input name="author" required></label><label>${html(context.t("Version"))}<input name="version" required value="1.0.0"></label>`, async(form)=>{
      const payload={team:state.team.members}; for(const key of ["name","description","author","version"]) payload[key]=form.get(key);
      const result=await context.action("export-team",payload); const bytes=Uint8Array.from(atob(result.content_base64),(char)=>char.charCodeAt(0)); const url=URL.createObjectURL(new Blob([bytes],{type:"application/zip"})); const link=document.createElement("a"); link.href=url; link.download=result.filename; link.click(); URL.revokeObjectURL(url); context.notify(context.t(result.message),"success");
    });
  }

  function importTeam() {
    if (!confirmDiscard()) return;
    const input=document.createElement("input"); input.type="file"; input.accept=".zip,application/zip";
    input.onchange=async()=>{const file=input.files?.[0]; if(!file)return; state.busy=true;render(); try{const bytes=new Uint8Array(await file.arrayBuffer()); let binary=""; for(let offset=0;offset<bytes.length;offset+=0x8000) binary+=String.fromCharCode(...bytes.subarray(offset,offset+0x8000)); const result=await context.action("import-team",{content_base64:btoa(binary)}); await refresh(result.key); context.notify(context.t(result.message),"success");}catch(error){context.notify(error.message||String(error),"error");}finally{state.busy=false;render();}}; input.click();
  }

  async function askAi() {
    if(!state.member)return; const className=state.member.class_name; const prompt=`\`\`\`python\n${state.code}\n\`\`\`\n\n${context.t("I want to implement:")}\n\n${context.t("Please modify the full {class_name} character automation code above.",{class_name:className})}\n\n${context.t("Return only the complete modified Python code for the whole file, not a patch and not an explanation.")}\n${state.member.base_char_url}`;
    try{await navigator.clipboard.writeText(prompt);context.notify(context.t("Ask AI template copied. Paste it into an AI chatbot."),"success");}catch(error){context.notify(error.message||String(error),"error");}
  }

  function bind() {
    container.querySelectorAll("[data-team]").forEach((button)=>button.onclick=()=>void loadTeam(button.dataset.team));
    container.querySelector("[data-member]")?.addEventListener("change",(event)=>void loadMember(event.target.value));
    const editor=container.querySelector("textarea"); editor?.addEventListener("input",()=>{state.code=editor.value;setDirty();updateStatus();}); editor?.addEventListener("keydown",(event)=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="s"){event.preventDefault();void save();} if(event.key==="Tab"){event.preventDefault();const start=editor.selectionStart,end=editor.selectionEnd;editor.setRangeText("    ",start,end,"end");state.code=editor.value;setDirty();updateStatus();}});
    container.querySelector("[data-action='create']")?.addEventListener("click",createTeam); container.querySelector("[data-action='delete']")?.addEventListener("click",()=>void deleteTeam()); container.querySelector("[data-action='import']")?.addEventListener("click",importTeam); container.querySelector("[data-action='export']")?.addEventListener("click",exportTeam); container.querySelector("[data-action='reset']")?.addEventListener("click",()=>void reset()); container.querySelector("[data-action='save']")?.addEventListener("click",()=>void save()); container.querySelector("[data-action='ask-ai']")?.addEventListener("click",()=>void askAi());
  }

  function updateStatus(){const node=container.querySelector(".team-code-status");if(!node)return;node.classList.toggle("dirty",dirty());node.textContent=dirty()?context.t("Unsaved changes"):(state.member?.is_builtin?context.t("Using built in code"):context.t("Team character code saved"));}
  function render(){if(state.destroyed)return; const selectedKey=state.team?.key; container.innerHTML=`<style>${STYLE}</style><section class="team-code-page"><aside class="team-code-sidebar"><h2 class="team-code-heading">${html(context.t("Teams"))}</h2><div class="team-code-list">${state.teams.map((team)=>`<button type="button" data-team="${html(team.key)}" class="${team.key===selectedKey?"active":""}">${html(team.display_name)}</button>`).join("")}</div><div class="team-code-actions"><button class="primary" data-action="create">${html(context.t("Create Team"))}</button><button class="danger" data-action="delete" ${!state.team||state.busy?"disabled":""}>${html(context.t("Delete Team"))}</button><button data-action="import">${html(context.t("Import Team"))}</button><button data-action="export" ${!state.team||state.busy?"disabled":""}>${html(context.t("Export Team"))}</button></div></aside><div class="team-code-workspace">${state.member?`<div class="team-code-toolbar"><div class="team-code-portrait">${state.member.image_data_url?`<img src="${html(state.member.image_data_url)}" alt="">`:""}</div><label>${html(context.t("Character Code"))}</label><select class="team-code-member" data-member ${state.busy?"disabled":""}>${state.team.members.map((name)=>`<option value="${html(name)}" ${name===state.member.class_name?"selected":""}>${html(context.t(state.characters.find((item)=>item.class_name===name)?.display_name||name))}</option>`).join("")}</select><span class="team-code-spacer"></span><button data-action="ask-ai">${html(context.t("Ask AI"))}</button></div><div class="team-code-editor"><textarea spellcheck="false" ${state.busy?"readonly":""}>${html(state.code)}</textarea></div><div class="team-code-footer"><span class="team-code-status"></span><button data-action="reset" ${state.busy?"disabled":""}>${html(context.t("Reset to Built In"))}</button><button class="primary" data-action="save" ${state.busy||!dirty()?"disabled":""}>${html(context.t("Save"))}</button></div>`:`<div></div><div class="team-code-empty">${html(context.t("Create or import a team to edit character code."))}</div><div></div>`}</div></section>`;bind();updateStatus();}

  context.registerSave(save);
  const unsubscribe=context.subscribe((event)=>{if(event.name==="teams-changed"&&!dirty())void refresh(event.payload?.key).then(render);if(event.name==="team-member-changed"&&event.payload?.class_name===state.member?.class_name&&!dirty()){state.member=event.payload;state.code=event.payload.code;state.cleanCode=event.payload.code;render();}});
  container.innerHTML=`<style>${STYLE}</style><div class="team-code-empty">${html(context.t("Loading"))}</div>`;
  void refresh().then(render).catch((error)=>{context.notify(error.message||String(error),"error");container.innerHTML=`<style>${STYLE}</style><div class="team-code-empty">${html(error.message||error)}</div>`;});
  return()=>{state.destroyed=true;unsubscribe();context.registerSave(null);context.setDirty(false);};
}
