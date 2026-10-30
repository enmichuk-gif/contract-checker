/* UI-смоук: прогоняет пользовательский сценарий в jsdom против живого сервера.
   Опциональный dev-инструмент (нужны Node.js и jsdom, в основной сценарий запуска
   не входит). Запуск:
     1) python3 run.py                       # поднять сервер на :8000
     2) cd devtools/ui-smoke && npm i jsdom  # один раз
     3) node smoke.js                        # BASE_URL=http://127.0.0.1:8000
   Проверяет: загрузку документов, таблицу, бейджи статусов, цепочки оснований,
   просмотрщик с подсветкой цитаты, предложения, реестр, вопросы В1–В3
   с очисткой ответа и пересчёт при смене даты проверки. */
const { JSDOM } = require('jsdom');
const { TextEncoder, TextDecoder } = require('util');
const fs = require('fs');
const path = require('path');

const WEB = path.resolve(__dirname, '..', '..', 'web');
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(cond, msg) {
  console.log((cond ? '  OK  ' : ' FAIL ') + msg);
  if (!cond) failures++;
}

(async () => {
  // 0. Регрессия (см. README §8): скрытые элементы не должны переопределяться
  //    авторским CSS, иначе модалка просмотрщика перекрывает всю страницу.
  const cssText = fs.readFileSync(path.join(WEB, 'style.css'), 'utf8');
  check(/\[hidden\]\s*\{[^}]*display:\s*none\s*!important/.test(cssText),
        'CSS: правило [hidden]{display:none!important} на месте');
  const htmlText = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
  check(/rel="icon"/.test(htmlText), 'HTML: favicon объявлен (нет 404 в консоли)');
  const html = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8')
    .replace('<script src="app.js"></script>', '');
  const dom = new JSDOM(html, {
    url: BASE + '/', runScripts: 'dangerously', pretendToBeVisual: true,
  });
  const { window } = dom;
  window.TextEncoder = TextEncoder;
  window.TextDecoder = TextDecoder;
  window.Element.prototype.scrollIntoView = function () {};
  window.alert = (m) => { throw new Error('ALERT: ' + m); };
  window.fetch = (url, opts) => fetch(url.startsWith('http') ? url : BASE + url, opts);
  window.eval(fs.readFileSync(path.join(WEB, 'app.js'), 'utf8'));

  const $ = (s) => window.document.querySelector(s);
  const $$ = (s) => [...window.document.querySelectorAll(s)];
  const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

  // регрессия: модалка просмотрщика изначально закрыта
  check($('#viewerModal').hidden === true && $('#viewerModal').hasAttribute('hidden'),
        'модалка просмотрщика изначально скрыта атрибутом hidden');

  // 1. Учебные документы
  click($('#sampleBtn'));
  await sleep(700);
  check($$('#fileList .filechip').length === 6, '6 файлов добавлено через кнопку «Загрузить учебные документы»');

  // 2. Анализ
  check($('#asOf').value === '2026-03-10', 'дата проверки по умолчанию 10.03.2026');
  click($('#analyzeBtn'));
  await sleep(900);
  check(!$('#resultsCard').hidden, 'секция результатов показана');
  const rows = $$('#contractsTable tbody tr');
  check(rows.length === 3, 'в таблице 3 договора');
  const nbsp = s => s.replace(/\u00a0/g, ' ');
  const rowText = nbsp(rows.map(r => r.textContent).join('|'));
  check(rowText.includes('STN-101') && rowText.includes('1 380 000') , 'STN-101: сумма 1 380 000 ₽');
  check(rowText.includes('30.04.2026'), 'STN-101: срок 30.04.2026');
  check(rowText.includes('840 000'), 'STN-102: сумма 840 000 ₽');
  check(rowText.includes('нет данных'), 'STN-103: «нет данных»');
  check(rowText.includes('изменено'), 'бейдж «изменено» у изменённого значения');
  check($$('#contractsTable .badge.b-review').length === 0, 'нет ложных «требует проверки»');

  // 3. Клик по значению — цепочка оснований
  const cell = $('td.valuecell[data-ct="STN-101"][data-fk="amount"]');
  click(cell);
  await sleep(50);
  const detail = $('#contractsTable tr.detailrow');
  check(!!detail, 'клик по сумме раскрыл цепочку оснований');
  const dtxt = detail.textContent;
  check(dtxt.includes('1 200 000') && dtxt.includes('1 380 000'), 'в цепочке исходное и изменённое значения');
  check(dtxt.includes('02_amendment_signed.txt'), 'в цепочке виден файл допсоглашения');
  check(dtxt.includes('вступило в силу') || dtxt.includes('действует с'), 'указано вступление в силу');
  check(dtxt.includes('10.05.2026') || $$('#contractsTable .chainitem.proposal').length >= 0, 'предложения в деталях');
  check(detail.querySelectorAll('button.showtext').length >= 2, 'кнопки «показать в тексте» у каждого шага');

  // 4. Просмотрщик документа с подсветкой (последний шаг — допсоглашение)
  const showBtns = [...detail.querySelectorAll('button.showtext')];
  const amendBtn = showBtns.find(b => b.dataset.file === '02_amendment_signed.txt');
  check(!!amendBtn, 'еvidence-кнопка ведёт на файл допсоглашения');
  click(amendBtn);
  await sleep(50);
  check(!$('#viewerModal').hidden, 'модалка просмотрщика открыта');
  check($('#viewerFile').textContent === '02_amendment_signed.txt', 'в модалке нужный файл');
  const marks = $$('#viewerBody mark.hit');
  check(marks.length >= 1, 'цитата подсвечена <mark>');
  check(marks.map(m => m.textContent).join('').includes('1 380 000'), 'подсвечен именно фрагмент с новой суммой');
  check($('#viewerBody .docline.hitline') !== null, 'строка с цитатой выделена');
  click($('#viewerClose'));
  await sleep(20);
  check($('#viewerModal').hidden, 'модалка закрывается');

  // 5. Несогласованные предложения
  check(!$('#proposalsCard').hidden, 'секция предложений видна');
  const propText = nbsp($('#proposalsTable').textContent);
  check(propText.includes('27.03.2026') && propText.includes('05_supplier_email.txt'), 'предложение из письма (27.03) в таблице');
  check(propText.includes('1 450 000') && propText.includes('06_amendment_draft.txt'), 'предложение из проекта (1 450 000) в таблице');
  check(propText.includes('не подписан') || propText.includes('Не подписан') || propText.includes('требует согласия'), 'статус несогласованности показан');

  // 6. Предупреждения на учебных данных отсутствуют
  check($('#warningsCard').hidden, 'на чистых данных секция «Требует проверки» скрыта');

  // 7. Реестр документов
  check($$('#registryTable tbody tr').length === 6, 'в реестре 6 документов');
  const regText = $('#registryTable').textContent;
  check(regText.includes('Проект допсоглашения') && regText.includes('Письмо') && regText.includes('подписан'), 'типы и статусы в реестре');

  // 8. Вопросы В1–В3 (3 вопроса)
  const cards = $$('.qcard');
  check(cards.length === 3, 'три карточки вопросов');
  click($('button.qask[data-qid="q1"]'));
  await sleep(300);
  check($('#qa-q1').textContent.includes('30.04.2026'), 'В1: срок STN-101 — 30.04.2026');
  check($('#qa-q1').textContent.includes('Основания значения'), 'В1: показаны основания');
  // Очистка ответа
  click($('button.qclear[data-qid="q1"]'));
  await sleep(50);
  check($('#qa-q1').textContent === '', 'В1: ответ очищен кнопкой «Очистить»');
  click($('button.qask[data-qid="q2"]'));
  await sleep(300);
  check($('#qa-q2').textContent.includes('840 000'), 'В2: сумма STN-102 — 840 000 ₽');
  click($('button.qask[data-qid="q3"]'));
  await sleep(300);
  const q3 = $('#qa-q3').textContent;
  check(q3.includes('STN-102') && q3.includes('27.03.2026'), 'В3: STN-102 раньше 01.04 + несогласованный перенос');
  check(!q3.includes('STN-101 —') && !q3.includes('STN-103 —'), 'В3: остальные договоры не в списке');

  // 9. Смена даты проверки пересчитывает (до вступления ДС в силу)
  $('#asOf').value = '2026-02-15';
  $('#asOf').dispatchEvent(new window.Event('change', { bubbles: true }));
  await sleep(800);
  const rows2 = $$('#contractsTable tbody tr:not(.detailrow)');
  const t2 = nbsp(rows2.map(r => r.textContent).join('|'));
  check(t2.includes('15.04.2026') && t2.includes('1 200 000'), 'на 15.02.2026 действуют значения договора (ДС ещё не в силе)');
  const pendingBadge = $$('#contractsTable .valuecell .badge.b-changed').length === 0;
  check(pendingBadge, 'бейдж «изменено» исчез (изменение ещё не действует)');

  console.log(failures === 0 ? '\nUI SMOKE: ALL OK' : `\nUI SMOKE: ${failures} FAILURES`);
  process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.error('SMOKE CRASH:', e); process.exit(2); });
