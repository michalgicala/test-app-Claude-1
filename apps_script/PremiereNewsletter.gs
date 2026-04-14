/**
 * Premiere Newsletter — Google Apps Script
 *
 * Reads book premieres from the 'premieres' tab in Google Sheets
 * and sends a monthly digest email grouped by publisher.
 * Blue-themed, clean design. All Polish characters preserved.
 *
 * ── SETUP (run once) ──────────────────────────────────────────────────────────
 * 1. script.google.com → New project → paste this file → save as "PremiereNewsletter"
 * 2. Project Settings → Script Properties → add:
 *      SPREADSHEET_ID  — ID from your Google Sheet URL
 *      RECIPIENT_EMAIL — your email address
 * 3. Run setupPremiereTrigger() once to schedule monthly sends on the 1st
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * For backfill (Jan–Apr 2026):
 *   After running the Python backfill script, trigger sendMonthlyPremiereNewsletter()
 *   manually — it will send one email per unsent month, oldest first.
 */

// ── Column indices (0-based, must match PREMIERES_HEADERS in config.py) ───────
var PCOL = {
  BOOK_ID:        0,
  TITLE:          1,
  AUTHOR:         2,
  PUBLISHER:      3,
  PREMIERE_MONTH: 4,
  URL:            5,
  COVER_URL:      6,
  ISBN:           7,
  DESCRIPTION:    8,
  TAGS:           9,
  EMAILED_MONTH:  10,
};

// ── Month labels in Polish ────────────────────────────────────────────────────

var MONTHS_NOM = [
  '', 'Styczeń', 'Luty', 'Marzec', 'Kwiecień', 'Maj', 'Czerwiec',
  'Lipiec', 'Sierpień', 'Wrzesień', 'Październik', 'Listopad', 'Grudzień',
];

// ── Main entry point ──────────────────────────────────────────────────────────

function sendMonthlyPremiereNewsletter() {
  var props     = PropertiesService.getScriptProperties();
  var sheetId   = props.getProperty('SPREADSHEET_ID');
  var recipient = props.getProperty('RECIPIENT_EMAIL');

  if (!sheetId || !recipient) {
    Logger.log('Błąd: SPREADSHEET_ID lub RECIPIENT_EMAIL nie są ustawione w Script Properties.');
    return;
  }

  var ss    = SpreadsheetApp.openById(sheetId);
  var sheet = ss.getSheetByName('premieres');
  if (!sheet) {
    Logger.log('Błąd: Nie znaleziono zakładki "premieres". Uruchom najpierw skrypt Python.');
    return;
  }

  var unsynced = getUnsyncedPremieres_(sheet);
  if (unsynced.length === 0) {
    Logger.log('Brak nowych premier do wysłania.');
    return;
  }

  // Group by premiere_month and send one email per month (oldest first)
  var byMonth = groupByMonth_(unsynced);
  var months  = Object.keys(byMonth).sort();

  months.forEach(function(monthKey) {
    var books   = byMonth[monthKey];
    var subject = buildSubject_(monthKey, books.length);
    var html    = buildHtmlEmail_(monthKey, books, sheetId);
    var plain   = buildPlainEmail_(monthKey, books);

    GmailApp.sendEmail(recipient, subject, plain, {
      htmlBody: html,
      name: 'Premiery Książkowe',
    });

    Logger.log('Wysłano: ' + monthKey + ' — ' + books.length + ' książek → ' + recipient);
    markEmailed_(sheet, books, monthKey);
  });
}

// ── Sheet reading ─────────────────────────────────────────────────────────────

function getUnsyncedPremieres_(sheet) {
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  return data.slice(1).reduce(function(acc, row, i) {
    var emailed = String(row[PCOL.EMAILED_MONTH] || '').trim();
    if (emailed === '') {
      acc.push({
        rowIndex:       i + 2,  // 1-based, skips header
        book_id:        String(row[PCOL.BOOK_ID]),
        title:          String(row[PCOL.TITLE]),
        author:         String(row[PCOL.AUTHOR]),
        publisher:      String(row[PCOL.PUBLISHER]),
        premiere_month: String(row[PCOL.PREMIERE_MONTH]),
        url:            String(row[PCOL.URL]),
        isbn:           String(row[PCOL.ISBN]         || ''),
        description:    String(row[PCOL.DESCRIPTION]  || ''),
        tags:           String(row[PCOL.TAGS]         || ''),
      });
    }
    return acc;
  }, []);
}

function groupByMonth_(books) {
  return books.reduce(function(acc, book) {
    var m = book.premiere_month;
    if (!acc[m]) acc[m] = [];
    acc[m].push(book);
    return acc;
  }, {});
}

// ── Mark as sent ──────────────────────────────────────────────────────────────

function markEmailed_(sheet, books, monthKey) {
  var col = PCOL.EMAILED_MONTH + 1;  // 1-based
  books.forEach(function(book) {
    sheet.getRange(book.rowIndex, col).setValue(monthKey);
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatMonthPl_(monthKey) {
  var parts = monthKey.split('-');
  var m     = parseInt(parts[1], 10);
  return (MONTHS_NOM[m] || monthKey) + ' ' + parts[0];
}

function groupByPublisher_(books) {
  var map = {};
  books.forEach(function(b) {
    var pub = b.publisher || 'Inne wydawnictwa';
    if (!map[pub]) map[pub] = [];
    map[pub].push(b);
  });
  return Object.keys(map).sort().map(function(pub) {
    return { publisher: pub, books: map[pub] };
  });
}

function buildSubject_(monthKey, count) {
  return 'Premiery książkowe — ' + formatMonthPl_(monthKey) + ' (' + count + ' tytułów)';
}

function esc_(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── HTML email (blue-themed) ──────────────────────────────────────────────────

function buildHtmlEmail_(monthKey, books, sheetId) {
  var monthLabel = formatMonthPl_(monthKey);
  var groups     = groupByPublisher_(books);
  var sheetUrl   = 'https://docs.google.com/spreadsheets/d/' + sheetId;

  var css = [
    // Reset & base
    'body{margin:0;padding:0;background:#ddeeff;font-family:Helvetica,Arial,sans-serif;color:#1a2e3b;font-size:14px;line-height:1.6}',
    // Outer wrapper
    '.outer{background:#ddeeff;padding:32px 16px}',
    // Card container
    '.wrap{max-width:640px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 4px 20px rgba(26,82,118,0.12)}',
    // Header band
    '.header{background:linear-gradient(135deg,#1a5276 0%,#2471a3 100%);padding:36px 36px 28px;color:#ffffff}',
    '.header h1{margin:0 0 4px;font-size:24px;font-weight:700;letter-spacing:-0.5px;color:#ffffff}',
    '.header .subtitle{margin:0;font-size:14px;color:#aed6f1;letter-spacing:0.2px}',
    // Body
    '.body{padding:28px 36px 20px}',
    // Publisher section
    '.pub-section{margin-bottom:32px}',
    '.pub-header{display:flex;align-items:center;gap:10px;margin-bottom:14px;border-bottom:2px solid #2471a3;padding-bottom:8px}',
    '.pub-name{font-size:15px;font-weight:700;color:#1a5276;text-transform:uppercase;letter-spacing:0.06em;flex:1}',
    '.pub-count{background:#2471a3;color:#fff;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:700;white-space:nowrap}',
    // Book card
    '.book{padding:14px 0 14px 14px;border-bottom:1px solid #ebf5fb;border-left:3px solid #aed6f1}',
    '.book:last-child{border-bottom:none}',
    '.book + .book{margin-top:4px}',
    '.book-title{font-size:15px;font-weight:700;margin:0 0 2px}',
    '.book-title a{color:#1a5276;text-decoration:none}',
    '.book-title a:hover{text-decoration:underline;color:#2471a3}',
    '.book-author{font-size:12px;color:#5d8aa8;margin:0 0 4px}',
    '.book-isbn{font-size:11px;color:#aab7c4;margin:0 0 2px}',
    '.book-tags{font-size:11px;color:#aab7c4;margin:4px 0 0}',
    // Expandable description
    'details{margin:6px 0 0}',
    'details summary{cursor:pointer;font-size:12px;font-weight:600;color:#2471a3;list-style:none;display:inline-block;padding:2px 6px;border:1px solid #aed6f1;border-radius:4px}',
    'details summary::-webkit-details-marker{display:none}',
    '.book-desc{font-size:13px;color:#3d5c70;line-height:1.6;margin:8px 0 0;padding:10px 12px;background:#ebf5fb;border-radius:4px}',
    // Link
    '.book-link{display:inline-block;margin-top:8px;font-size:12px;color:#2471a3;text-decoration:none;font-weight:600}',
    '.book-link:hover{text-decoration:underline}',
    // Footer
    '.footer{background:#ebf5fb;padding:18px 36px;font-size:11px;color:#7f9aaa;line-height:1.9;border-top:1px solid #d6eaf8}',
    '.footer a{color:#2471a3;text-decoration:none}',
    '.footer a:hover{text-decoration:underline}',
  ].join('');

  var html = '<!DOCTYPE html><html lang="pl"><head>' +
    '<meta charset="UTF-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>Premiery książkowe — ' + esc_(monthLabel) + '</title>' +
    '<style>' + css + '</style>' +
    '</head><body><div class="outer"><div class="wrap">';

  // ── Header ──────────────────────────────────────────────────────────────────
  html += '<div class="header">' +
    '<h1>Premiery książkowe</h1>' +
    '<p class="subtitle">' + esc_(monthLabel) +
    ' &nbsp;·&nbsp; ' + books.length + ' nowych tytułów' +
    '</p></div>';

  // ── Body ─────────────────────────────────────────────────────────────────────
  html += '<div class="body">';

  groups.forEach(function(group) {
    html += '<div class="pub-section">' +
      '<div class="pub-header">' +
      '<span class="pub-name">' + esc_(group.publisher) + '</span>' +
      '<span class="pub-count">' + group.books.length + '</span>' +
      '</div>';

    group.books.forEach(function(book) {
      html += '<div class="book">' +
        '<div class="book-title"><a href="' + esc_(book.url) + '">' + esc_(book.title) + '</a></div>' +
        '<div class="book-author">' + esc_(book.author) + '</div>';

      if (book.isbn) {
        html += '<div class="book-isbn">ISBN: ' + esc_(book.isbn) + '</div>';
      }

      if (book.description) {
        html += '<details><summary>Opis ▾</summary>' +
          '<div class="book-desc">' + esc_(book.description) + '</div>' +
          '</details>';
      }

      if (book.tags) {
        html += '<div class="book-tags">' + esc_(book.tags) + '</div>';
      }

      html += '<a href="' + esc_(book.url) + '" class="book-link">Zobacz na lubimyczytac.pl →</a>' +
        '</div>';
    });

    html += '</div>';  // .pub-section
  });

  html += '</div>';  // .body

  // ── Footer ───────────────────────────────────────────────────────────────────
  html += '<div class="footer">' +
    'Archiwum premier: <a href="' + sheetUrl + '">Google Sheets</a> &nbsp;·&nbsp; ' +
    'Newsletter generowany automatycznie przez Book Discovery.<br>' +
    'Wybrane wydawnictwa: Znak (i sub-marki), Marginesy, Czwarta Strona, Wydawnictwo Poznańskie, ' +
    'Jaguar, Wydawnictwo Kobiece, Otwarte, W.A.B., Filia, Sine Qua Non, PWN.' +
    '</div>';

  html += '</div></div></body></html>';
  return html;
}

// ── Plain-text email ──────────────────────────────────────────────────────────

function buildPlainEmail_(monthKey, books) {
  var monthLabel = formatMonthPl_(monthKey);
  var groups     = groupByPublisher_(books);
  var lines      = [];

  lines.push('Premiery książkowe — ' + monthLabel);
  lines.push(books.length + ' nowych tytułów');
  lines.push('');
  lines.push('='.repeat(52));
  lines.push('');

  groups.forEach(function(group) {
    lines.push('[ ' + group.publisher.toUpperCase() + ' — ' + group.books.length + ' tytułów ]');
    lines.push('-'.repeat(48));
    group.books.forEach(function(book, i) {
      lines.push((i + 1) + '. ' + book.title);
      if (book.author) lines.push('   ' + book.author);
      if (book.isbn)   lines.push('   ISBN: ' + book.isbn);
      lines.push('   ' + book.url);
      if (book.description) {
        var desc = book.description.length > 300
          ? book.description.slice(0, 297) + '...'
          : book.description;
        lines.push('   Opis: ' + desc);
      }
      lines.push('');
    });
  });

  lines.push('='.repeat(52));
  lines.push('Newsletter wysyłany automatycznie przez Book Discovery.');

  return lines.join('\n');
}

// ── One-time setup ────────────────────────────────────────────────────────────

/**
 * Run ONCE from the Apps Script editor to schedule monthly newsletter.
 * Editor → select "setupPremiereTrigger" in the function dropdown → Run ▶
 */
function setupPremiereTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'sendMonthlyPremiereNewsletter') {
      ScriptApp.deleteTrigger(t);
    }
  });
  ScriptApp.newTrigger('sendMonthlyPremiereNewsletter')
    .timeBased()
    .onMonthDay(1)
    .atHour(9)
    .create();
  Logger.log('Trigger ustawiony: sendMonthlyPremiereNewsletter — 1. dzień miesiąca o ~09:00.');
}
