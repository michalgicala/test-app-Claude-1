/**
 * Book Discovery — Google Apps Script
 *
 * Reads new books written by the Python scraper from Google Sheets
 * and sends a digest email via GmailApp.
 * No passwords required — authenticates via your Google account automatically.
 *
 * ── SETUP (run once) ──────────────────────────────────────────────────────────
 * 1. script.google.com → New project → paste this file → save
 * 2. Project Settings → Script Properties → add the three properties below
 * 3. Run setupTrigger() once from the editor to schedule daily checks
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * Script Properties (set in Project Settings → never in code):
 *   SPREADSHEET_ID  — ID from your Google Sheet URL
 *   RECIPIENT_EMAIL — address that receives the digest
 *   GEMINI_API_KEY  — (optional) free key from aistudio.google.com/apikey
 */

// ── Column indices (0-based, must match Python BOOKS_HEADERS order) ───────────
var COL = {
  BOOK_ID:        0,
  TITLE:          1,
  AUTHOR:         2,
  CATEGORY:       3,
  RATING:         4,
  RATINGS_COUNT:  5,
  URL:            6,
  ISBN:           7,
  COVER_URL:      8,
  DESCRIPTION:    9,
  DESCRIPTION_AI: 10,
  TAGS:           11,
  FIRST_SEEN:     12,
  EMAILED_DATE:   13,
  EMPIK_URL:      14,
  ALREADY_READ:   15,
};

// ── Main entry point (called by trigger) ──────────────────────────────────────

function sendNewBooksDigest() {
  var props     = PropertiesService.getScriptProperties();
  var sheetId   = props.getProperty('SPREADSHEET_ID');
  var recipient = props.getProperty('RECIPIENT_EMAIL');
  var geminiKey = props.getProperty('GEMINI_API_KEY') || '';

  if (!sheetId || !recipient) {
    Logger.log('Błąd: SPREADSHEET_ID lub RECIPIENT_EMAIL nie są ustawione w Script Properties.');
    return;
  }

  var ss    = SpreadsheetApp.openById(sheetId);
  var sheet = ss.getSheetByName('books');
  if (!sheet) {
    Logger.log('Błąd: Nie znaleziono arkusza "books".');
    return;
  }

  var newBooks = getUnemailedBooks_(sheet);
  if (newBooks.length === 0) {
    Logger.log('Brak nowych książek do wysłania.');
    return;
  }

  var totalInDb = Math.max(0, sheet.getLastRow() - 1);
  var subject   = buildSubject_(newBooks);
  var htmlBody  = buildHtmlEmail_(newBooks, totalInDb, sheetId);
  var plainBody = buildPlainEmail_(newBooks, totalInDb, sheetId);

  GmailApp.sendEmail(recipient, subject, plainBody, {
    htmlBody: htmlBody,
    name: 'Book Discovery',
  });

  Logger.log('Email wysłany do ' + recipient + ' — ' + newBooks.length + ' nowych książek.');
  markAsEmailed_(sheet, newBooks);
}

// ── Read sheet ────────────────────────────────────────────────────────────────

function getUnemailedBooks_(sheet) {
  var data = sheet.getDataRange().getValues();
  if (data.length <= 1) return [];

  return data.slice(1).reduce(function(acc, row, i) {
    var emailed     = String(row[COL.EMAILED_DATE] || '').trim();
    var alreadyRead = String(row[COL.ALREADY_READ] || '').trim().toUpperCase();
    if (emailed === '' && alreadyRead !== 'TRUE') {
      acc.push({
        rowIndex:      i + 2, // 1-based, skip header
        book_id:       String(row[COL.BOOK_ID]),
        title:         String(row[COL.TITLE]),
        author:        String(row[COL.AUTHOR]),
        category:      String(row[COL.CATEGORY]),
        rating:        parseFloat(row[COL.RATING]) || 0,
        ratingsCount:  parseInt(row[COL.RATINGS_COUNT], 10) || 0,
        url:           String(row[COL.URL]),
        coverUrl:      String(row[COL.COVER_URL] || ''),
        description:   String(row[COL.DESCRIPTION] || ''),
        descriptionAi: String(row[COL.DESCRIPTION_AI] || ''),
        empikUrl:      String(row[COL.EMPIK_URL] || ''),
      });
    }
    return acc;
  }, []);
}

// ── Mark as emailed ───────────────────────────────────────────────────────────

function markAsEmailed_(sheet, books) {
  var today      = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  var emailedCol = COL.EMAILED_DATE + 1; // 1-based
  books.forEach(function(book) {
    sheet.getRange(book.rowIndex, emailedCol).setValue(today);
  });
}

// ── Gemini AI descriptions ────────────────────────────────────────────────────

function enrichWithGemini_(books, apiKey) {
  if (!apiKey) return books;
  books.slice(0, 50).forEach(function(book, i) {
    if (book.descriptionAi && book.descriptionAi.length > 20) return;
    var hook = callGemini_(book, apiKey);
    if (hook) book.descriptionAi = hook;
    if (i < books.length - 1) Utilities.sleep(600);
  });
  return books;
}

function callGemini_(book, apiKey) {
  var url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key=' + apiKey;
  var body = {
    system_instruction: { parts: [{ text:
      'Jesteś ekspertem od rekomendacji książek non-fiction. ' +
      'Piszesz krótkie, konkretne zachęty do czytania po polsku. ' +
      'Używaj polskich znaków: ą, ę, ó, ś, ź, ż, ć, ń, ł.'
    }]},
    contents: [{ parts: [{ text:
      'Tytuł: ' + book.title + '\n' +
      'Autor: ' + book.author + '\n' +
      'Kategoria: ' + book.category + '\n' +
      'Ocena: ' + book.rating + '/10 (' + book.ratingsCount + ' ocen)\n' +
      'Opis: ' + book.description.slice(0, 500) + '\n\n' +
      'Napisz 2-3 zdania po polsku zachęcające do przeczytania tej książki. ' +
      'Skup się na tym, co czytelnik zyska lub czego się dowie.'
    }]}],
  };
  try {
    var resp = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json; charset=utf-8',
      payload: JSON.stringify(body),
      muteHttpExceptions: true,
    });
    if (resp.getResponseCode() !== 200) return '';
    var json = JSON.parse(resp.getContentText());
    return json.candidates[0].content.parts[0].text.trim();
  } catch (e) {
    Logger.log('Gemini error dla "' + book.title + '": ' + e);
    return '';
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function pickBookOfFortnight_(books) {
  if (!books.length) return null;
  return books.slice().sort(function(a, b) {
    return (b.rating * Math.log10(Math.max(b.ratingsCount, 1))) -
           (a.rating * Math.log10(Math.max(a.ratingsCount, 1)));
  })[0];
}

function groupByCategory_(books) {
  var map = {};
  books.forEach(function(b) {
    if (!map[b.category]) map[b.category] = [];
    map[b.category].push(b);
  });
  return Object.keys(map)
    .sort(function(a, b) { return map[b].length - map[a].length; })
    .map(function(cat) { return { category: cat, books: map[cat] }; });
}

function buildSubject_(books) {
  var today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'd MMM yyyy');
  return 'Nowe ksiazki non-fiction [' + today + '] — ' + books.length + ' nowych pozycji';
}

function nextRunDate_() {
  var d   = new Date();
  var day = d.getDate();
  var next = day < 15
    ? new Date(d.getFullYear(), d.getMonth(), 15)
    : new Date(d.getFullYear(), d.getMonth() + 1, 1);
  return Utilities.formatDate(next, Session.getScriptTimeZone(), 'd MMM yyyy');
}

function esc_(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Minimalist HTML email ─────────────────────────────────────────────────────

function buildHtmlEmail_(books, totalInDb, sheetId) {
  var fortnight = pickBookOfFortnight_(books);
  var groups    = groupByCategory_(books);
  var today     = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'd MMM yyyy');
  var sheetUrl  = 'https://docs.google.com/spreadsheets/d/' + sheetId;

  var css = [
    'body{margin:0;padding:0;background:#ffffff;font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;font-size:14px;line-height:1.6}',
    '.wrap{max-width:600px;margin:0 auto;padding:32px 24px}',
    'h1{font-size:18px;font-weight:600;margin:0 0 4px;color:#1a1a1a}',
    '.meta{font-size:12px;color:#888;margin:0 0 32px}',
    'hr{border:none;border-top:1px solid #e5e5e5;margin:24px 0}',
    '.label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#888;margin:0 0 12px}',
    '.featured{border-left:3px solid #1a1a1a;padding-left:16px;margin-bottom:8px}',
    '.book-title{font-size:15px;font-weight:600;margin:0 0 2px}',
    '.book-title a{color:#1a1a1a;text-decoration:none}',
    '.book-title a:hover{text-decoration:underline}',
    '.author{font-size:12px;color:#666;margin:0 0 4px}',
    '.rating{font-size:12px;color:#666;margin:0 0 8px}',
    '.hook{font-size:13px;color:#333;margin:0 0 8px;line-height:1.55}',
    '.links{font-size:12px;margin:0 0 4px}',
    '.links a{color:#1a1a1a;margin-right:12px}',
    '.book-item{padding:14px 0;border-bottom:1px solid #f0f0f0}',
    '.book-item:last-child{border-bottom:none}',
    '.cat-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#888;margin:20px 0 4px}',
    '.footer{font-size:11px;color:#aaa;margin-top:32px;line-height:1.8}',
    '.footer a{color:#888}',
  ].join('');

  var html = '<!DOCTYPE html><html lang="pl"><head>' +
    '<meta charset="UTF-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<style>' + css + '</style>' +
    '</head><body><div class="wrap">';

  // Header
  html += '<h1>Nowe książki non-fiction</h1>' +
    '<p class="meta">' + today + ' &nbsp;·&nbsp; ' + books.length + ' nowych &nbsp;·&nbsp; ' + totalInDb + ' łącznie w bazie</p>';

  // Book of the Fortnight
  if (fortnight) {
    html += '<div class="label">Książka dwutygodnia</div>' +
      '<div class="featured">' +
      '<div class="book-title"><a href="' + esc_(fortnight.url) + '">' + esc_(fortnight.title) + '</a></div>' +
      '<div class="author">' + esc_(fortnight.author) + '</div>' +
      '<div class="rating">' + fortnight.rating.toFixed(1) + '/10 &nbsp;(' + fortnight.ratingsCount + ' ocen)</div>' +
      '<div class="links"><a href="' + esc_(fortnight.url) + '">lubimyczytac.pl</a>' +
      (fortnight.empikUrl ? '<a href="' + esc_(fortnight.empikUrl) + '">Empik</a>' : '') +
      '</div></div>';
    html += '<hr>';
  }

  // Grouped books
  groups.forEach(function(group) {
    html += '<div class="cat-label">' + esc_(group.category) + ' (' + group.books.length + ')</div>';
    group.books.forEach(function(book) {
      if (fortnight && book.book_id === fortnight.book_id) return;
      html += '<div class="book-item">' +
        '<div class="book-title"><a href="' + esc_(book.url) + '">' + esc_(book.title) + '</a></div>' +
        '<div class="author">' + esc_(book.author) + '</div>' +
        '<div class="rating">' + book.rating.toFixed(1) + '/10 &nbsp;(' + book.ratingsCount + ' ocen)</div>' +
        '<div class="links"><a href="' + esc_(book.url) + '">lubimyczytac.pl</a>' +
        (book.empikUrl ? '<a href="' + esc_(book.empikUrl) + '">Empik</a>' : '') +
        '</div></div>';
    });
  });

  // Footer
  html += '<div class="footer">' +
    'Baza danych: <a href="' + sheetUrl + '">Google Sheets</a><br>' +
    'Aby oznaczyć jako przeczytaną: wpisz TRUE w kolumnie <em>already_read</em> w arkuszu.<br>' +
    'Następne skanowanie: ok. ' + nextRunDate_() +
    '</div>';

  html += '</div></body></html>';
  return html;
}

// ── Plain-text email ──────────────────────────────────────────────────────────

function buildPlainEmail_(books, totalInDb, sheetId) {
  var fortnight = pickBookOfFortnight_(books);
  var groups    = groupByCategory_(books);
  var today     = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'd MMM yyyy');
  var lines     = [];

  lines.push('Nowe ksiazki non-fiction | ' + today);
  lines.push(books.length + ' nowych | ' + totalInDb + ' lacznie w bazie');
  lines.push('');

  if (fortnight) {
    lines.push('KSIAZKA DWUTYGODNIA');
    lines.push('---');
    lines.push(fortnight.title + ' — ' + fortnight.author);
    lines.push('Ocena: ' + fortnight.rating.toFixed(1) + '/10 (' + fortnight.ratingsCount + ' ocen)');
    lines.push(fortnight.url);
    if (fortnight.empikUrl) lines.push('Empik: ' + fortnight.empikUrl);
    lines.push('');
  }

  groups.forEach(function(group) {
    lines.push(group.category.toUpperCase() + ' (' + group.books.length + ')');
    lines.push('---');
    var idx = 1;
    group.books.forEach(function(book) {
      if (fortnight && book.book_id === fortnight.book_id) return;
      lines.push(idx + '. ' + book.title + ' — ' + book.author);
      lines.push('   ' + book.rating.toFixed(1) + '/10 (' + book.ratingsCount + ' ocen)');
      lines.push('   ' + book.url);
      if (book.empikUrl) lines.push('   Empik: ' + book.empikUrl);
      lines.push('');
      idx++;
    });
  });

  lines.push('---');
  lines.push('Baza: https://docs.google.com/spreadsheets/d/' + sheetId);
  lines.push('Nastepne skanowanie: ok. ' + nextRunDate_());
  lines.push('Przeczytana ksiazka? Wpisz TRUE w kolumnie already_read w arkuszu.');

  return lines.join('\n');
}

// ── One-time setup ────────────────────────────────────────────────────────────

/**
 * Run ONCE to schedule daily checks.
 * The function checks every day but sends email only when there are new books.
 * Editor → Run → setupTrigger
 */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'sendNewBooksDigest') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sendNewBooksDigest')
    .timeBased()
    .everyDays(1)
    .atHour(10)
    .create();
  Logger.log('Trigger ustawiony: sendNewBooksDigest bedzie uruchamiana codziennie o ~10:00.');
}
