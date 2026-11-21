const styleURL = new URL('./accounts_v3.css', import.meta.url).href;

const chinese = {
  'Multi-account configuration': '多账号配置', 'Add account': '新增账号', 'Save configuration': '保存配置',
  'Start': '开始执行', 'Stop': '停止执行', 'Reload': '重新加载', 'Cancel': '取消',
  'Run accounts in card order. A failed task stops the remaining accounts.': '按卡片顺序执行。任务失败时停止后续账号。',
  'No accounts configured': '尚未配置账号', 'New account': '新账号', 'Account': '账号',
  'Enabled': '启用', 'Account ID': '账号标识', 'Leave empty to keep the saved ID': '留空保留已保存的标识',
  'Enter the login list text with every * removed. Keep its original case.': '填写登录列表中的文字，去掉所有 *，保留大小写。',
  'Task': '执行任务', 'None (switch account only)': '无（仅切换账号）', 'Unavailable task': '任务不可用',
  'Remove': '删除', 'Move up': '上移', 'Move down': '下移', 'Not run': '未运行', 'Completed': '已完成',
  'Success': '成功', 'Failed': '失败', 'Stopped': '已停止', 'Running': '执行中', 'Current account': '当前账号',
  'Save failed': '保存失败', 'Configuration saved': '配置已保存', 'Loading…': '正在加载…',
  'Unsaved changes': '有未保存的更改', 'Enter one value per line': '每行填写一个值',
  'Account configuration changed. Reload before saving.': '配置已在其他位置更改，请重新加载后再保存。',
  'Check account IDs, duplicate rows and task settings.': '请检查账号标识是否有效、是否重复，以及任务配置。',
  'Account configuration is in use.': '运行期间无法保存配置。',
  'No account task is running.': '当前没有正在执行的多账号任务。',
  'Could not stop account task.': '无法停止多账号任务。',
  'Could not read or write account configuration or history.': '无法读取或写入多账号配置或运行记录。',
  'The cloud adapter uses one signed-in browser profile and does not support switching accounts.': '当前云端适配器使用单一登录会话，暂不支持切换账号。',
  'Discard changes and reload?': '放弃未保存的更改并重新加载？',
  'Account task running': '正在执行多账号任务', 'Account run history': '账号运行记录',
  'Account settings have changed elsewhere. Reload to see the latest plan.': '配置已在其他位置更改，请重新加载。',
  'Add item': '添加项目', 'Choose an item': '请选择项目',
  'Elapsed': '用时', 'No enabled accounts': '没有启用的账号',
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, action, className) {
  const node = element('button', text, className);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}

function taskId(reference) {
  return reference ? reference.module + '.' + reference.class : '';
}

function fieldVisible(key, fields, values, parents = []) {
  if (parents.includes(key)) return false;
  return fields.every(field => {
    const rules = field.children || [];
    if (!rules.some(rule => rule.keys.includes(key))) return true;
    const selected = [].concat(values[field.key] ?? field.value);
    return fieldVisible(field.key, fields, values, [...parents, key])
      && rules.some(rule => selected.includes(rule.choice) && rule.keys.includes(key));
  });
}

function configInput(field, value, change, tr) {
  const options = field.options || [];
  if (field.kind === 'select') {
    const input = element('select');
    options.forEach((option, i) => input.add(new Option(tr(String(option)), String(i), false, option === value)));
    input.onchange = () => change(options[input.selectedIndex]);
    return input;
  }
  if (field.kind === 'multi_selection') {
    const group = element('div', undefined, 'acct-options');
    const choices = options.map(option => {
      const label = element('label');
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.checked = value.includes(option);
      label.append(checkbox, tr(String(option)));
      group.append(label);
      return checkbox;
    });
    choices.forEach(input => { input.onchange = () => change(options.filter((_, i) => choices[i].checked)); });
    return group;
  }
  if (field.kind === 'list' && options.length) {
    const group = element('div', undefined, 'acct-values');
    const items = [...value];
    function draw() {
      group.replaceChildren();
      items.forEach((item, i) => {
        const row = element('div', undefined, 'acct-value');
        row.append(element('span', tr(String(item))), button(tr('Remove'), () => {
          items.splice(i, 1);
          change([...items]);
          draw();
        }));
        group.append(row);
      });
      const select = element('select');
      select.setAttribute('aria-label', tr('Add item'));
      select.add(new Option(tr('Choose an item'), ''));
      options.forEach((option, i) => {
        if (field.allow_duplication || !items.includes(option)) select.add(new Option(tr(String(option)), String(i)));
      });
      select.onchange = () => {
        if (select.value === '') return;
        items.push(options[Number(select.value)]);
        change([...items]);
        draw();
      };
      group.append(select);
    }
    draw();
    return group;
  }
  if (field.kind === 'multiline' || field.kind === 'list') {
    const input = element('textarea');
    input.value = Array.isArray(value) ? value.join('\n') : value;
    if (field.kind === 'list') input.placeholder = tr('Enter one value per line');
    input.oninput = () => change(field.kind === 'list' ? input.value.split('\n').filter(Boolean) : input.value);
    return input;
  }
  const input = element('input');
  if (field.kind === 'boolean') {
    input.type = 'checkbox';
    input.checked = value;
    input.onchange = () => change(input.checked);
  } else if (field.kind === 'integer' || field.kind === 'number') {
    input.type = 'number';
    input.required = true;
    input.step = field.kind === 'integer' ? '1' : 'any';
    if (field.minimum != null) input.min = field.minimum;
    if (field.maximum != null) input.max = field.maximum;
    input.value = value;
    input.oninput = () => change(input.valueAsNumber);
  } else {
    input.value = value ?? '';
    input.oninput = () => change(input.value);
  }
  return input;
}

export function mountAccounts(host, api) {
  const zh = String(api.language).startsWith('zh');
  const tr = text => (zh && chinese[text]) || api.t(text);
  const msg = error => {
    const message = error.message || String(error);
    const row = message.match(/^Invalid configuration in account row (\d+)\.$/);
    return zh && row ? '请检查第 ' + row[1] + ' 行的账号和任务配置。' : tr(message);
  };
  const style = element('link');
  style.rel = 'stylesheet';
  style.href = styleURL;
  document.head.append(style);
  let rows = [], tasks = [], state = {}, version = '';
  let disposed = false, working = true, dirty = false, stale = false, polling = false, epoch = 0;
  const root = element('section', undefined, 'acct-page');
  const header = element('header', undefined, 'acct-header');
  const title = element('h2', tr('Multi-account configuration'));
  const actions = element('div', undefined, 'acct-actions');
  const save = button(tr('Save configuration'), submit, 'acct-primary');
  const start = element('button', tr('Start'));
  start.type = 'button';
  start.disabled = true;
  const stop = button(tr('Stop'), stopTask);
  const reload = button(tr('Reload'), () => {
    if (!dirty || window.confirm(tr('Discard changes and reload?'))) load();
  });
  const add = button(tr('Add account'), () => {
    rows.push(newRow({index: null, task: null, config: {}, enabled: true}));
    markDirty();
    renderCards();
    cards.lastElementChild?.querySelector('input[type=password]')?.focus();
  });
  actions.append(start, stop, reload, save);
  header.append(title, actions);
  const feedback = element('p', tr('Loading…'), 'acct-feedback');
  feedback.setAttribute('role', 'status');
  const status = element('p', '', 'acct-status');
  const runNotice = element('p', '', 'acct-hint');
  runNotice.id = 'acct-run-notice';
  start.setAttribute('aria-describedby', runNotice.id);
  const form = element('fieldset', undefined, 'acct-form');
  const cards = element('div', undefined, 'acct-cards');
  form.append(cards, add);
  root.append(header, element('p', tr('Run accounts in card order. A failed task stops the remaining accounts.'), 'acct-hint'), runNotice, feedback, status, form);
  host.replaceChildren(root);

  function newRow(row) {
    return {...row, opened: row.index === null, account_key: '', drafts: new Map([[taskId(row.task), {...row.config}]])};
  }
  function markDirty() {
    dirty = true;
    api.setDirty(true);
    feedback.textContent = tr(stale ? 'Account settings have changed elsewhere. Reload to see the latest plan.' : 'Unsaved changes');
  }
  function resultText(row) {
    const result = row.index != null && !stale ? state.results?.[row.index] : null;
    if (!result) return tr('Not run');
    const when = new Date(result.finished_at);
    const stamp = Number.isNaN(when.getTime()) ? '' : when.toLocaleString(zh ? 'zh-CN' : 'en-US');
    return [tr({success: 'Success', failed: 'Failed', cancelled: 'Stopped'}[result.status]), stamp].filter(Boolean).join(' · ');
  }
  function controls() {
    form.disabled = working || state.busy || !version;
    save.disabled = form.disabled || stale;
    runNotice.textContent = state.run_unavailable ? tr(state.run_unavailable) : '';
    runNotice.hidden = !runNotice.textContent;
    start.title = runNotice.textContent;
    stop.disabled = working || !state.can_stop;
    reload.disabled = working;
    status.hidden = !(state.busy || state.start_time);
    const parts = [tr(state.phase || (state.busy ? 'Account task running' : 'Account run history')), tr('Completed') + ' ' + (state.completed || 0)];
    if (state.elapsed != null) {
      const minutes = Math.floor(state.elapsed / 60), seconds = state.elapsed % 60;
      parts.push(tr('Elapsed') + ' ' + minutes + ':' + String(seconds).padStart(2, '0'));
    }
    if (state.busy && state.current_row != null) parts.push(tr('Current account') + ' #' + (state.current_row + 1));
    status.textContent = parts.join(' · ');
    rows.forEach(row => { if (row.resultLabel) row.resultLabel.textContent = resultText(row); });
  }
  function renderCards() {
    cards.replaceChildren();
    if (!rows.length) cards.append(element('p', tr('No accounts configured'), 'acct-empty'));
    rows.forEach((row, position) => {
      const card = element('details', undefined, 'acct-card');
      card.open = row.opened;
      card.ontoggle = () => { row.opened = card.open; };
      const summary = element('summary');
      const title = row.index == null ? tr('New account') : tr('Account') + ' ' + (position + 1) + ' · ' + row.label;
      const selectedTask = tasks.find(spec => taskId(spec.task) === taskId(row.task));
      const description = selectedTask ? tr(selectedTask.name) : tr(row.task ? 'Unavailable task' : 'None (switch account only)');
      row.resultLabel = element('small', resultText(row));
      summary.append(element('strong', title), element('span', description), row.resultLabel);
      const body = element('div', undefined, 'acct-body');
      const enabledLabel = element('label', undefined, 'acct-toggle');
      const enabled = element('input');
      enabled.type = 'checkbox';
      enabled.checked = row.enabled;
      enabled.onchange = () => { row.enabled = enabled.checked; markDirty(); controls(); };
      enabledLabel.append(enabled, tr('Enabled'));
      const key = element('input');
      key.type = 'password';
      key.autocomplete = 'new-password';
      key.spellcheck = false;
      key.required = row.index == null;
      key.value = row.account_key;
      if (row.index != null) key.placeholder = tr('Leave empty to keep the saved ID');
      key.oninput = () => { row.account_key = key.value; markDirty(); };
      const keyLabel = element('label', tr('Account ID'), 'acct-field');
      keyLabel.append(key, element('small', tr('Enter the login list text with every * removed. Keep its original case.')));
      const taskSelect = element('select');
      taskSelect.add(new Option(tr('None (switch account only)'), ''));
      tasks.forEach(task => taskSelect.add(new Option(tr(task.name), taskId(task.task))));
      if (row.task && !selectedTask) taskSelect.add(new Option(tr('Unavailable task') + ' · ' + row.task.class, taskId(row.task)));
      taskSelect.value = taskId(row.task);
      taskSelect.onchange = () => {
        row.task = tasks.find(task => taskId(task.task) === taskSelect.value)?.task || null;
        if (!row.drafts.has(taskSelect.value)) row.drafts.set(taskSelect.value, {});
        row.opened = true;
        markDirty();
        renderCards();
      };
      const taskLabel = element('label', tr('Task'), 'acct-field');
      taskLabel.append(taskSelect);
      body.append(enabledLabel, keyLabel, taskLabel);
      if (selectedTask) {
        const values = row.drafts.get(taskId(row.task));
        const fields = selectedTask.fields;
        const settings = element('div', undefined, 'acct-task-fields');
        function renderFields() {
          settings.replaceChildren();
          fields.filter(field => fieldVisible(field.key, fields, values)).forEach(field => {
            const label = element('div', undefined, 'acct-field');
            const caption = element('label', tr(field.key));
            const input = configInput(field, values[field.key] ?? field.value, value => {
              values[field.key] = value;
              markDirty();
              if (field.children) renderFields();
            }, tr);
            const id = 'acct-' + position + '-field-' + fields.indexOf(field);
            input.id = id;
            caption.htmlFor = id;
            label.append(caption, input);
            if (field.description) label.append(element('small', tr(field.description)));
            settings.append(label);
          });
        }
        renderFields();
        body.append(settings);
      }
      function move(offset) {
        rows.splice(position, 1);
        rows.splice(position + offset, 0, row);
        markDirty();
        renderCards();
      }
      const footer = element('div', undefined, 'acct-actions');
      const up = button(tr('Move up'), () => move(-1)), down = button(tr('Move down'), () => move(1));
      up.disabled = position === 0;
      down.disabled = position === rows.length - 1;
      footer.append(up, down, button(tr('Remove'), () => { rows.splice(position, 1); markDirty(); renderCards(); }));
      body.append(footer);
      card.append(summary, body);
      cards.append(card);
    });
    controls();
  }
  function accept(data) {
    version = data.version;
    rows = data.accounts.map(newRow);
    tasks = data.tasks;
    state = data;
    stale = dirty = false;
    api.setDirty(false);
    renderCards();
  }
  async function load() {
    ++epoch;
    working = true;
    controls();
    try {
      const data = await api.request('/api/accounts');
      if (disposed) return;
      accept(data);
      feedback.textContent = '';
    } catch (error) { if (!disposed) feedback.textContent = msg(error); }
    finally { if (!disposed) { working = false; controls(); } }
  }
  async function submit() {
    if (working || state.busy || !version || stale) return false;
    for (const input of form.querySelectorAll('input,select,textarea')) {
      if (!input.checkValidity()) {
        input.closest('details').open = true;
        input.reportValidity();
        return false;
      }
    }
    ++epoch;
    working = true;
    controls();
    const accounts = rows.map(row => ({
      index: row.index, enabled: row.enabled, task: row.task, config: row.drafts.get(taskId(row.task)),
      ...(row.account_key || row.index === null ? {account_key: row.account_key} : {}),
    }));
    try {
      const data = await api.request('/api/accounts', {
        method: 'PUT', body: {version, accounts},
      });
      if (disposed) return false;
      accept(data);
      feedback.textContent = tr('Configuration saved');
      api.notify(tr('Configuration saved'));
      return true;
    } catch (error) {
      if (!disposed) {
        feedback.textContent = msg(error);
        api.notify(feedback.textContent);
      }
      return false;
    }
    finally { if (!disposed) { working = false; controls(); } }
  }
  async function stopTask() {
    ++epoch;
    working = true;
    controls();
    try {
      const data = await api.request('/api/accounts/stop', {method: 'POST'});
      if (!disposed) { state = data; feedback.textContent = tr('Stopped'); }
    } catch (error) { if (!disposed) feedback.textContent = msg(error); }
    finally { if (!disposed) { working = false; controls(); } }
  }
  async function poll() {
    if (!version || disposed || working || polling) return;
    polling = true;
    const generation = epoch;
    try {
      const data = await api.request('/api/accounts/status');
      if (disposed || epoch !== generation) return;
      state = data;
      if (version !== data.version) {
        stale = true;
        feedback.textContent = tr('Account settings have changed elsewhere. Reload to see the latest plan.');
      }
      controls();
    } catch (error) { if (!disposed && epoch === generation) feedback.textContent = msg(error); }
    finally { polling = false; }
  }
  api.registerSave(submit);
  load();
  const timer = setInterval(poll, 2500);
  return () => {
    disposed = true;
    clearInterval(timer);
    api.registerSave(null);
    api.setDirty(false);
    rows = [];
    root.remove();
    style.remove();
  };
}
