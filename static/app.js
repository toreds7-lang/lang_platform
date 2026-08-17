const state = {
  videoId: null,
  // The language being practised. Chosen before a video is loaded, and part of
  // that video's cache key from then on, so every later request carries it.
  // Picking a video out of the history sets this from the video's own metadata
  // rather than the other way round -- which is what stops a cached video ever
  // being reopened under the wrong language.
  lang: "en",
  // code -> {name, native, voices, has_reading}, from GET /api/languages.
  langs: {},
  // video_id -> meta, so the history dropdown can restore a video's language.
  history: {},
  voice: null,
  // Slow speech is produced by the voice, not by slowing playback down, so
  // tones and pitch accent survive it. Applies to every clip the app plays.
  slowSpeech: false,
  showReading: true,
  raw: [],
  modified: [],
  chunkToSentence: [],
  bookmarks: new Set(),
  // Shadowing scores, one entry per sentence, index-aligned with state.modified.
  // The 20% cut is made here rather than server-side, so the ratio control can
  // re-slice instantly without another scoring pass.
  recommendScores: [],
  recommendRatio: 0.2,
  recommendedSet: new Set(),
  mode: "modified",
  // The transcript view the toggle button flips between, remembered so that
  // leaving Bookmarks returns you to the one you were reading.
  transcriptMode: "modified",
  currentSentenceIdx: null,
  // Esc freezes the highlight on the sentence you are on and pauses the video,
  // so TTS / E / G target it instead of whatever playback has moved on to.
  // Only a flag: the pinned sentence IS state.currentSentenceIdx, held still.
  pinned: false,
  autoPauseEnabled: true,
  // A sentence's end time is the next one's start time, so the tick that fires
  // auto-pause is already inside sentence N+1. With this on, the highlight is
  // held on the sentence that just finished, which is the one you want R / TTS
  // to repeat. Off restores the older behaviour of letting it slide forward.
  stayOnSentence: true,
  autoPauseTargetEnd: null,
  autoPauseArmedIdx: null,
  ytPlayer: null,
  ytReady: false,
  pendingVideoId: null,
  pollHandle: null,
  // The line under the mouse. Alt+S / Alt+G prefer this over the playing
  // sentence, because playback has usually moved on by the time you ask.
  hoveredSentenceIdx: null,
  // Which explanation the popup is showing, plus a sequence number so a slow
  // response for an earlier request can't overwrite a newer one.
  explain: { open: false, mode: null, sentenceIdx: null, word: null, text: null, seq: 0 },
  // mode+idx(+word) -> result, so re-opening a view is instant.
  explainCache: new Map(),
  // Whole-video Q&A. The conversation lives here and nowhere else: the server
  // is stateless, so Clear is just emptying this array, and a reload or a
  // video switch starts fresh. seq plays the same role as explain.seq below.
  chat: { messages: [], busy: false, seq: 0 },
};

const el = {
  input: document.getElementById("video-input"),
  loadBtn: document.getElementById("load-btn"),
  langSelect: document.getElementById("lang-select"),
  historySelect: document.getElementById("history-select"),
  readingToggle: document.getElementById("reading-toggle"),
  videoTitle: document.getElementById("video-title"),
  themeToggle: document.getElementById("theme-toggle"),
  uiLangToggle: document.getElementById("ui-lang-toggle"),
  status: document.getElementById("transcript-status"),
  list: document.getElementById("transcript-list"),
  transcriptModeBtn: document.getElementById("transcript-mode-btn"),
  bookmarksModeBtn: document.getElementById("bookmarks-mode-btn"),
  recommendBtn: document.getElementById("recommend-btn"),
  recommendRatio: document.getElementById("recommend-ratio"),
  regenModifiedBtn: document.getElementById("regen-modified-btn"),
  regenRawBtn: document.getElementById("regen-raw-btn"),
  flushDictBtn: document.getElementById("flush-dict-btn"),
  voiceToggle: document.getElementById("voice-toggle"),
  ttsBtn: document.getElementById("tts-play-btn"),
  slowToggle: document.getElementById("slow-toggle"),
  autopauseToggle: document.getElementById("autopause-toggle"),
  stayToggle: document.getElementById("stay-toggle"),
  stayLabel: document.getElementById("stay-label"),
  popup: document.getElementById("explain-popup"),
  popupOverlay: document.getElementById("popup-overlay"),
  popupClose: document.getElementById("popup-close"),
  popupMode: document.getElementById("popup-mode"),
  popupTitle: document.getElementById("popup-title"),
  popupBody: document.getElementById("popup-body"),
  chatLog: document.getElementById("chat-log"),
  chatMeta: document.getElementById("chat-meta"),
  chatClear: document.getElementById("chat-clear"),
  chatChips: document.getElementById("chat-chips"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  chatSend: document.getElementById("chat-send"),
  toast: document.getElementById("toast"),
};

// ---- UI language (interface chrome + explanation language) ----
// One switch drives two things: every button/label around the app, and the
// language word/sentence/grammar/breakdown explanations and chat answers come
// back in. They are kept locked together on purpose -- a reader fluent enough
// in Korean to want a Korean interface wants Korean explanations too, and
// splitting the two into separate settings would double the state for no
// benefit anyone asked for.
const STRINGS = {
  en: {
    appTitle: "Shadowing Practice — Anybody can learn Any language",
    langSelectTitle: "The language you are practising. Pick it before loading a video.",
    videoInputPlaceholder: "Paste a YouTube URL or video ID...",
    loadBtn: "Load",
    historyDefaultOption: "-- Recent videos --",
    uiLangToggleTitle: "Switch interface and explanation language",
    themeToggleTitle: "Switch between light and dark theme",
    themeToggleDark: "🌙 Dark",
    themeToggleLight: "☀️ Light",
    bookmarksModeBtn: "⭐ Bookmarks",
    modeModified: "Modified",
    modeOriginal: "Original",
    modeSwitchTitle: "Switch to {to}",
    recommendBtn: "🎯 Recommend sentences",
    recommendBtnTitle: "Score every sentence and mark the best ones to shadow",
    recommendRatioTitle: "How much of the transcript to mark",
    ratioTop10: "Top 10%",
    ratioTop20: "Top 20%",
    ratioTop30: "Top 30%",
    readingToggle: "あ Reading",
    readingToggleTitle: "Show or hide the pronunciation above each word",
    regenModifiedBtn: "🔄 Regenerate sentences",
    regenModifiedBtnTitle: "Regenerate the modified (sentence) transcript",
    regenRawBtn: "🔄 Re-download raw",
    regenRawBtnTitle: "Re-download the raw transcript",
    flushDictBtn: "🗑 Clear explanation cache",
    flushDictBtnTitle: "Clear cached word, sentence and grammar explanations",
    transcriptStatusInitial: "Load a video to begin.",
    colResizerTitle: "Drag to resize the columns · double-click to reset",
    ttsPlayBtn: "🔊 Play sentence (TTS)",
    slowToggle: "🐢 Slow",
    slowToggleTitle:
      "Speak slowly. The voice itself slows down, so tones and pitch accent stay correct — unlike slowing the audio down afterwards.",
    autopauseLabel: "Auto-pause at sentence end",
    stayLabel: "Stay on sentence",
    stayLabelTitle:
      "When auto-pause fires, hold the highlight on the sentence that just played instead of letting it slide onto the next one, so R / TTS repeats what you just heard.",
    rowResizerTitle: "Drag to resize the video · double-click to reset",
    chatTitle: "💬 Ask about this video",
    chatClearBtn: "🗑 Clear",
    chatClearTitle: "Clear this conversation",
    chipSummarize: "📝 Summarize",
    chipQuiz: "❓ Quiz me",
    chipFlowchart: "🔀 Flowchart",
    chipSummarizePrompt: "Summarize this video in 5 to 8 bullet points. Cite [mm:ss] for each point.",
    chipQuizPrompt:
      "Make 5 multiple-choice questions about this video. Give four options A to D for each one, " +
      "then list the correct answers under an '### Answers' heading at the end.",
    chipFlowchartPrompt:
      "Draw the structure of this video as a mermaid flowchart TD diagram inside a ```mermaid fence. " +
      "Then add one or two short sentences explaining it.",
    chatInputPlaceholder: "Ask about this video...",
    chatSendBtn: "Send",
    shortcutsSummary: "⌨ Shortcuts",
    shortcutsHelpHtml: `<strong>Modified mode shortcuts:</strong> A = previous sentence &nbsp; S = repeat sentence &nbsp; D = next sentence
      <br><strong>Speak aloud:</strong> R = read the sentence with TTS
      <br><strong>Speak a selection:</strong> select any text, then T = speak it in the language you are learning &nbsp; Shift+T = speak it in English. Works inside the explanation popup and the chat too.
      <br><strong>Esc</strong> = pin this sentence &amp; pause the video, so TTS keeps speaking it instead of following playback. Esc again to resume.
      <br><strong>Explanations:</strong> double-click a word &nbsp; E = sentence in easy English &nbsp; G = grammar
      <br><strong>Breakdown:</strong> O (or Alt+O) = translate the sentence, then gloss it part by part, with a dictionary-form vocabulary list you can copy straight into Anki. Select a phrase first to break down just that phrase.
      <br>R, E, G and O all target the sentence your mouse is on (or the highlighted one). Alt+S also works for E.`,
    popupHintHtml: `Double-click a word &middot; point at a sentence + E for easy English &middot; G for grammar &middot; O for a part-by-part breakdown &middot; Esc to close
  <br>Drag the bottom-right corner to widen this window &mdash; the width is remembered.`,
    ytErr2: "Invalid video ID.",
    ytErr5: "This video can't be played in the HTML5 player.",
    ytErr100: "Video not found (it may have been removed or made private).",
    ytErr101: "The video owner doesn't allow this video to be played in embedded players.",
    ytErr150: "The video owner doesn't allow this video to be played in embedded players.",
    ytErrGeneric: "YouTube player error (code {code}).",
    playbackError: "Playback error: {msg}",
    statusLoadingTranscript: "Loading transcript...",
    statusSegmenting: "Segmenting into sentences (LLM)...",
    statusLoadError: "Error: {message}",
    noBookmarks: "No bookmarks yet. Star a sentence while shadowing to save it here.",
    recBadgeWorth: "Worth shadowing",
    explainPointHint: "Point the mouse at a sentence, or play one, then press E, G or O.",
    explainLoading: "Loading…",
    explainModeWord: "Word",
    explainModeSentence: "Sentence",
    explainModeGrammar: "Grammar",
    explainModeBreakdown: "Breakdown",
    labelMeaningHere: "Meaning here",
    labelExample: "Example",
    labelEasyEnglish: "In simple English",
    labelWhatItMeans: "What it means",
    labelStructure: "Structure",
    labelTense: "Tense",
    labelGrammarPoints: "Grammar points",
    labelInEnglish: "In English",
    labelPartByPart: "Part by part",
    labelVocab: "Vocabulary (dictionary form)",
    sayHearWord: "Hear this word on its own",
    sayHearExample: "Hear this example sentence",
    sayHearForm: "Hear this form",
    sayHearPart: "Hear this part",
    sayHearPassage: "Hear the whole passage",
    sayHearDefault: "Hear this",
    ankiCopyBtn: "📋 Copy for Anki",
    ankiCopyTitle: "Copy every word as word;meaning;explanation, one card per line",
    ankiCopyFail: "Could not copy: {message}",
    ankiCopySuccess: (v) =>
      `Copied ${v.n} word${v.n === 1 ? "" : "s"} for Anki. Import with ";" as the separator.`,
    speakSelectEmpty: "Select some text first, then press T.",
    speakTooLong: "That selection is too long to speak ({n} characters).",
    speakFailToast: "Could not speak that: {message}",
    ttsFailedGeneric: "TTS failed",
    recommendScoring: "Scoring sentences for shadowing value...",
    recommendFail: "Could not score sentences: {message}",
    regenModifiedStatus: "Regenerating sentence segmentation...",
    regenRawStatus: "Re-downloading raw transcript...",
    chatEmptyWithVideo: "Ask a question, or try one of the buttons below.",
    chatEmptyNoVideo: "Load a video to start asking questions.",
    chatNeedVideo: "Load a video first.",
    chatTokensCost: "~{k}k tokens ~${cost}/msg",
    chatTruncatedWarn: "⚠ Only the first {time} of this video · {rest}",
    diagramLoadFail: "Could not load the diagram library.",
    diagramDrawFail: "Could not draw this diagram.",
    requestFailed: "Request failed ({status}).",
  },
  ko: {
    appTitle: "쉐도잉 연습 — 누구나 어떤 언어든 배울 수 있다",
    langSelectTitle: "연습할 언어입니다. 영상을 불러오기 전에 선택하세요.",
    videoInputPlaceholder: "유튜브 URL이나 동영상 ID를 붙여넣으세요...",
    loadBtn: "불러오기",
    historyDefaultOption: "-- 최근 영상 --",
    uiLangToggleTitle: "인터페이스 및 설명 언어 전환",
    themeToggleTitle: "라이트/다크 테마 전환",
    themeToggleDark: "🌙 다크",
    themeToggleLight: "☀️ 라이트",
    bookmarksModeBtn: "⭐ 북마크",
    modeModified: "수정됨",
    modeOriginal: "원본",
    modeSwitchTitle: "{to}(으)로 전환",
    recommendBtn: "🎯 문장 추천",
    recommendBtnTitle: "모든 문장을 채점하여 쉐도잉하기 가장 좋은 문장을 표시합니다",
    recommendRatioTitle: "대본 중 표시할 비율",
    ratioTop10: "상위 10%",
    ratioTop20: "상위 20%",
    ratioTop30: "상위 30%",
    readingToggle: "あ 읽기",
    readingToggleTitle: "각 단어 위의 발음 표시를 켜고 끕니다",
    regenModifiedBtn: "🔄 문장 재생성",
    regenModifiedBtnTitle: "수정된(문장 단위) 대본을 다시 생성합니다",
    regenRawBtn: "🔄 원본 다시 받기",
    regenRawBtnTitle: "원본 대본을 다시 내려받습니다",
    flushDictBtn: "🗑 설명 캐시 지우기",
    flushDictBtnTitle: "저장된 단어·문장·문법 설명을 모두 지웁니다",
    transcriptStatusInitial: "시작하려면 영상을 불러오세요.",
    colResizerTitle: "드래그하여 열 너비 조절 · 더블클릭하면 초기화",
    ttsPlayBtn: "🔊 문장 재생 (TTS)",
    slowToggle: "🐢 느리게",
    slowToggleTitle:
      "천천히 말합니다. 음성 자체가 느려지므로, 나중에 오디오 속도를 늦추는 것과 달리 성조와 억양이 정확하게 유지됩니다.",
    autopauseLabel: "문장이 끝나면 자동 일시정지",
    stayLabel: "문장에 머무르기",
    stayLabelTitle:
      "자동 일시정지가 발생하면 강조 표시를 다음 문장으로 넘기지 않고 방금 재생된 문장에 유지하여, R / TTS가 방금 들은 내용을 반복하도록 합니다.",
    rowResizerTitle: "드래그하여 영상 크기 조절 · 더블클릭하면 초기화",
    chatTitle: "💬 이 영상에 대해 질문하기",
    chatClearBtn: "🗑 지우기",
    chatClearTitle: "이 대화를 지웁니다",
    chipSummarize: "📝 요약",
    chipQuiz: "❓ 퀴즈 내기",
    chipFlowchart: "🔀 순서도",
    chipSummarizePrompt:
      "이 영상을 5~8개의 요점으로 요약해 주세요. 각 요점마다 [mm:ss] 형식으로 시간을 표시해 주세요.",
    chipQuizPrompt:
      "이 영상에 대한 객관식 문제 5개를 만들어 주세요. 각 문제마다 A~D 네 개의 보기를 제시하고, " +
      "마지막에 '### 정답' 제목 아래 정답을 정리해 주세요.",
    chipFlowchartPrompt:
      "이 영상의 구조를 ```mermaid 코드 블록 안에 flowchart TD 형식의 다이어그램으로 그려 주세요. " +
      "그런 다음 이를 설명하는 짧은 문장을 한두 개 추가해 주세요.",
    chatInputPlaceholder: "이 영상에 대해 질문하세요...",
    chatSendBtn: "보내기",
    shortcutsSummary: "⌨ 단축키",
    shortcutsHelpHtml: `<strong>수정 모드 단축키:</strong> A = 이전 문장 &nbsp; S = 문장 반복 &nbsp; D = 다음 문장
      <br><strong>소리내어 읽기:</strong> R = TTS로 문장 읽기
      <br><strong>선택한 부분 읽기:</strong> 텍스트를 선택한 뒤 T = 학습 중인 언어로 읽기 &nbsp; Shift+T = 영어로 읽기. 설명 팝업과 채팅 안에서도 동작합니다.
      <br><strong>Esc</strong> = 이 문장을 고정하고 영상을 일시정지하여, TTS가 재생을 따라가지 않고 이 문장을 계속 읽도록 합니다. 다시 Esc를 누르면 재생을 이어갑니다.
      <br><strong>설명:</strong> 단어를 더블클릭 &nbsp; E = 쉬운 설명으로 문장 보기 &nbsp; G = 문법
      <br><strong>분석:</strong> O (또는 Alt+O) = 문장을 번역한 뒤 부분별로 풀이하고, Anki에 바로 복사할 수 있는 사전형 어휘 목록도 함께 보여줍니다. 문구를 먼저 선택하면 그 부분만 분석합니다.
      <br>R, E, G, O는 모두 마우스가 놓인 문장(또는 강조 표시된 문장)을 대상으로 합니다. Alt+S도 E와 동일하게 동작합니다.`,
    popupHintHtml: `단어를 더블클릭 &middot; 문장에 마우스를 놓고 E로 쉬운 설명 &middot; G로 문법 &middot; O로 부분별 분석 &middot; Esc로 닫기
  <br>오른쪽 아래 모서리를 드래그하면 창 너비를 조절할 수 있습니다 &mdash; 너비는 저장됩니다.`,
    ytErr2: "잘못된 동영상 ID입니다.",
    ytErr5: "이 동영상은 HTML5 플레이어에서 재생할 수 없습니다.",
    ytErr100: "동영상을 찾을 수 없습니다 (삭제되었거나 비공개로 전환되었을 수 있습니다).",
    ytErr101: "동영상 소유자가 이 동영상을 외부 사이트에 삽입하여 재생하는 것을 허용하지 않습니다.",
    ytErr150: "동영상 소유자가 이 동영상을 외부 사이트에 삽입하여 재생하는 것을 허용하지 않습니다.",
    ytErrGeneric: "유튜브 플레이어 오류 (코드 {code}).",
    playbackError: "재생 오류: {msg}",
    statusLoadingTranscript: "대본을 불러오는 중...",
    statusSegmenting: "문장으로 나누는 중 (LLM)...",
    statusLoadError: "오류: {message}",
    noBookmarks: "아직 북마크가 없습니다. 쉐도잉하면서 문장에 별표를 눌러 저장하세요.",
    recBadgeWorth: "쉐도잉할 가치가 있음",
    explainPointHint: "마우스를 문장 위에 놓거나 문장을 재생한 뒤 E, G, O 중 하나를 누르세요.",
    explainLoading: "불러오는 중…",
    explainModeWord: "단어",
    explainModeSentence: "문장",
    explainModeGrammar: "문법",
    explainModeBreakdown: "분석",
    labelMeaningHere: "여기서의 의미",
    labelExample: "예문",
    labelEasyEnglish: "쉬운 설명",
    labelWhatItMeans: "실제 의미",
    labelStructure: "구조",
    labelTense: "시제",
    labelGrammarPoints: "문법 포인트",
    labelInEnglish: "번역",
    labelPartByPart: "부분별 분석",
    labelVocab: "어휘 (사전형)",
    sayHearWord: "이 단어만 따로 듣기",
    sayHearExample: "이 예문 듣기",
    sayHearForm: "이 형태 듣기",
    sayHearPart: "이 부분 듣기",
    sayHearPassage: "전체 구절 듣기",
    sayHearDefault: "듣기",
    ankiCopyBtn: "📋 Anki용 복사",
    ankiCopyTitle: "각 단어를 단어;의미;설명 형식으로 한 줄씩 복사합니다",
    ankiCopyFail: "복사하지 못했습니다: {message}",
    ankiCopySuccess: (v) => `단어 ${v.n}개를 Anki용으로 복사했습니다. 구분자 ";"로 가져오세요.`,
    speakSelectEmpty: "먼저 텍스트를 선택한 뒤 T를 누르세요.",
    speakTooLong: "선택한 텍스트가 너무 길어서 읽을 수 없습니다 ({n}자).",
    speakFailToast: "읽을 수 없습니다: {message}",
    ttsFailedGeneric: "TTS 실패",
    recommendScoring: "쉐도잉 가치를 기준으로 문장을 채점하는 중...",
    recommendFail: "문장을 채점하지 못했습니다: {message}",
    regenModifiedStatus: "문장 분할을 다시 생성하는 중...",
    regenRawStatus: "원본 대본을 다시 받는 중...",
    chatEmptyWithVideo: "질문을 입력하거나 아래 버튼을 눌러보세요.",
    chatEmptyNoVideo: "질문을 시작하려면 영상을 먼저 불러오세요.",
    chatNeedVideo: "먼저 영상을 불러오세요.",
    chatTokensCost: "~{k}k 토큰 ~${cost}/메시지",
    chatTruncatedWarn: "⚠ 이 영상의 처음 {time}만 반영됨 · {rest}",
    diagramLoadFail: "다이어그램 라이브러리를 불러오지 못했습니다.",
    diagramDrawFail: "이 다이어그램을 그리지 못했습니다.",
    requestFailed: "요청이 실패했습니다 ({status}).",
  },
};

const UI_LANG_KEY = "shadowing.uilang.v1";
let uiLang = "en";

// {key} placeholders are substituted from vars; a value may also be a function
// of vars, for the one case (Anki's word count) that needs real pluralization
// logic rather than a fixed template.
function t(key, vars) {
  const table = STRINGS[uiLang] || STRINGS.en;
  let val = key in table ? table[key] : STRINGS.en[key];
  if (val === undefined) return key;
  if (typeof val === "function") return val(vars || {});
  if (vars) {
    for (const k in vars) val = val.split(`{${k}}`).join(vars[k]);
  }
  return val;
}

function uiLangToggleLabel() {
  return uiLang === "ko" ? "EN" : "한국어";
}

// The language name shown in the target-language picker: Korean when the UI
// is in Korean and the profile has one, its own native-script name otherwise.
function langLabel(prof) {
  return uiLang === "ko" && prof.name_ko ? prof.name_ko : prof.native;
}

function populateLangSelect() {
  el.langSelect.innerHTML = "";
  for (const prof of Object.values(state.langs)) {
    const opt = document.createElement("option");
    opt.value = prof.code;
    opt.textContent = langLabel(prof);
    el.langSelect.appendChild(opt);
  }
}

function applyUiLang(lang) {
  uiLang = lang === "ko" ? "ko" : "en";
  document.documentElement.lang = uiLang;
  document.title = t("appTitle");

  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((node) => {
    node.title = t(node.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-html]").forEach((node) => {
    node.innerHTML = t(node.dataset.i18nHtml);
  });

  el.uiLangToggle.textContent = uiLangToggleLabel();

  // These re-render from state rather than a static attribute, so the walk
  // above can't reach them.
  applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  syncModeButtons();
  if (!state.chat.messages.length) renderChatEmptyState();
  if (Object.keys(state.langs).length) {
    populateLangSelect();
    el.langSelect.value = state.lang;
    refreshHistory();
  }
}

function loadUiLang() {
  let lang = "en";
  try {
    lang = localStorage.getItem(UI_LANG_KEY) || "en";
  } catch {
    lang = "en";
  }
  applyUiLang(lang);
}

el.uiLangToggle.addEventListener("click", () => {
  const next = uiLang === "ko" ? "en" : "ko";
  applyUiLang(next);
  try {
    localStorage.setItem(UI_LANG_KEY, next);
  } catch {
    /* private mode / quota - the toggle still works, it just won't persist */
  }
});

// A brief message that doesn't disturb anything. #transcript-status is the
// transcript panel's own line and already carries loading, error and empty-
// bookmark states, so borrowing it for passing feedback would fight those.
let toastHandle = null;

function toast(message) {
  el.toast.textContent = message;
  el.toast.classList.remove("hidden");
  clearTimeout(toastHandle);
  toastHandle = setTimeout(() => el.toast.classList.add("hidden"), 2200);
}

// Every per-video endpoint is keyed by (language, video id), so the language
// rides along on all of them. The server can fall back to scanning the cache
// when it is missing, but that is a safety net for a hand-typed URL -- the app
// itself always knows which language it loaded a video under.
function videoUrl(path, params = {}) {
  const qs = new URLSearchParams({ lang: state.lang, explain_lang: uiLang, ...params });
  return `/api/videos/${state.videoId}${path}?${qs}`;
}

function fmtTime(t) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

// Exact inverse of escapeHtml, for text that is on its way OUT of the HTML
// string and into a plain-text consumer. textContent -> innerHTML escapes only
// these four, and &amp; must be undone last so that an escaped "&gt;" written
// literally by the model (stored as "&amp;gt;") comes back as "&gt;" rather
// than turning into a ">".
function unescapeHtml(str) {
  return str
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&");
}

// Punctuation to shave off the edges of a token so the lookup key is the bare
// word. \p{L}\p{N} rather than \w on purpose: JavaScript's \w is ASCII-only, so
// it would treat every accented and every CJK character as punctuation --
// "đẹp" strips down to "p", and a Chinese word strips to nothing at all.
const EDGE_PUNCT = /^[^\p{L}\p{N}']+|[^\p{L}\p{N}']+$/gu;

// One clickable span per whitespace token, with surrounding punctuation left
// outside it. This is the path every space-separated language takes.
function renderSpacedWordsHtml(text) {
  return text
    .split(/(\s+)/)
    .map((token) => {
      if (/^\s+$/.test(token) || token === "") return token;
      const clean = token.replace(EDGE_PUNCT, "");
      if (!clean) return escapeHtml(token);
      const lead = token.slice(0, token.indexOf(clean));
      const trail = token.slice(token.indexOf(clean) + clean.length);
      return `${escapeHtml(lead)}<span class="word" data-word="${escapeHtml(clean)}">${escapeHtml(clean)}</span>${escapeHtml(trail)}`;
    })
    .join("");
}

// Languages whose words can't be found by splitting on spaces get an explicit
// word list from the server, optionally carrying a reading. <ruby> is used
// rather than hand-positioned markup because the browser already places the
// reading correctly, including across a line wrap -- and hiding it is then a
// single CSS rule instead of a re-render.
// A reading that just repeats the word teaches nothing and doubles the line
// height. The server drops these now, but transcripts segmented before it did
// still carry them, so they are ignored here too rather than regenerated.
function usefulReading(word, reading) {
  if (!reading) return false;
  const bare = (s) => s.replace(/[^\p{L}\p{N}]/gu, "");
  return bare(reading) !== bare(word);
}

function renderGroupedWordsHtml(words) {
  return words
    .map(({ w, r }) => {
      const attr = escapeHtml(w.replace(EDGE_PUNCT, "") || w);
      const inner = usefulReading(w, r)
        ? `<ruby>${escapeHtml(w)}<rt>${escapeHtml(r)}</rt></ruby>`
        : escapeHtml(w);
      return `<span class="word" data-word="${attr}">${inner}</span>`;
    })
    .join("");
}

// The selected text, with ruby readings taken back out.
//
// getSelection().toString() serializes every node in the range, and <rt> is an
// ordinary node -- so selecting 今日はいい天気 yields 今日きょうはいい天気てんき,
// the same doubling you get copying furigana'd text off a web page. Speaking
// that verbatim would read every reading twice. Cloning the range and deleting
// the <rt>/<rp> elements first is the only way to get the base text back.
function selectionText() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return "";

  const frag = document.createDocumentFragment();
  for (let i = 0; i < sel.rangeCount; i++) {
    frag.appendChild(sel.getRangeAt(i).cloneContents());
  }
  const raw = frag.textContent;
  frag.querySelectorAll("rt, rp").forEach((node) => node.remove());
  const clean = frag.textContent.replace(/\s+/g, " ").trim();

  // Selecting ONLY the furigana strips down to nothing; in that case the
  // reading itself is what you asked to hear.
  return clean || raw.replace(/\s+/g, " ").trim();
}

function renderWordsHtml(sentence, text) {
  const words = sentence && sentence.words;
  if (words && words.length) return renderGroupedWordsHtml(words);
  // Raw caption view, or a segmentation that came back without a word list.
  // Splitting an unspaced script on whitespace would make the whole line one
  // unclickable token, so it falls back to one span per character instead.
  if (state.langs[state.lang] && state.langs[state.lang].spaced === false) {
    return renderGroupedWordsHtml([...text].filter((c) => !/\s/.test(c)).map((w) => ({ w })));
  }
  return renderSpacedWordsHtml(text);
}

// ---- YouTube IFrame API ----
window.onYouTubeIframeAPIReady = function () {
  state.ytReady = true;
  if (state.pendingVideoId) {
    createPlayer(state.pendingVideoId);
    state.pendingVideoId = null;
  }
};

function ytErrorMessage(code) {
  const key = `ytErr${code}`;
  return key in STRINGS.en ? t(key) : t("ytErrGeneric", { code });
}

function onPlayerError(e) {
  const msg = ytErrorMessage(e.data);
  console.error("YouTube player error:", e.data, msg);
  el.status.textContent = t("playbackError", { msg });
  el.status.classList.remove("hidden");
}

function createPlayer(videoId) {
  if (state.ytPlayer) {
    state.ytPlayer.loadVideoById(videoId);
    return;
  }
  state.ytPlayer = new YT.Player("yt-player", {
    videoId,
    playerVars: { rel: 0 },
    events: {
      onReady: () => startPolling(),
      onError: onPlayerError,
    },
  });
}

function loadPlayer(videoId) {
  if (!state.ytReady) {
    state.pendingVideoId = videoId;
    return;
  }
  createPlayer(videoId);
}

function startPolling() {
  if (state.pollHandle) return;
  state.pollHandle = setInterval(pollTick, 100);
}

function pollTick() {
  if (!state.ytPlayer || typeof state.ytPlayer.getPlayerState !== "function") return;
  if (state.ytPlayer.getPlayerState() !== YT.PlayerState.PLAYING) return;
  // However playback was resumed - Esc, A/S/D, or YouTube's own button - the
  // pin is released, so the flag can never drift out of sync with the player.
  if (state.pinned) setPinned(false);
  const t = state.ytPlayer.getCurrentTime();
  const idx = findSentenceAtTime(t);

  // Whether to stop is decided before the highlight moves. A sentence ends
  // exactly where the next one starts, so by the time the clock passes the
  // pause target it already reads as the next sentence: advancing first would
  // leave playback stopped with the wrong line selected for TTS.
  const stopping = state.autoPauseTargetEnd !== null && t >= state.autoPauseTargetEnd;
  const holding = stopping && state.stayOnSentence && state.autoPauseArmedIdx !== null;
  const focusIdx = holding ? state.autoPauseArmedIdx : idx;

  if (focusIdx !== null && focusIdx !== state.currentSentenceIdx) {
    state.currentSentenceIdx = focusIdx;
    if (state.mode === "modified" || state.mode === "bookmarks") highlightActiveLine(focusIdx);
  }

  // Arm auto-pause for whatever sentence is playing, even when playback was
  // started from the player itself rather than from a sentence click.
  if (state.autoPauseEnabled && state.autoPauseTargetEnd === null && idx !== null && idx !== state.autoPauseArmedIdx) {
    const sentence = state.modified[idx];
    if (sentence && t < sentence.end) {
      state.autoPauseTargetEnd = sentence.end;
      state.autoPauseArmedIdx = idx;
    }
  }

  if (stopping) {
    state.ytPlayer.pauseVideo();
    state.autoPauseTargetEnd = null;
    // armedIdx stays put: resuming lands in the next sentence, which no longer
    // matches it, so the block above re-arms on the very next tick.
  }
}

function findSentenceAtTime(t) {
  const list = state.modified;
  if (!list.length || t < list[0].start) return null;
  for (let i = 0; i < list.length; i++) {
    const next = list[i + 1];
    if (t >= list[i].start && (!next || t < next.start)) return i;
  }
  return list.length - 1;
}

function highlightActiveLine(sentenceIdx, scroll = true) {
  document.querySelectorAll(".sentence-line.active").forEach((n) => n.classList.remove("active", "pinned"));
  const line = el.list.querySelector(`.sentence-line[data-sentence-idx="${sentenceIdx}"]`);
  if (line) {
    line.classList.add("active");
    line.classList.toggle("pinned", state.pinned);
    if (scroll) line.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

// The highlighted line is what Alt+S / Alt+G explain, so anything that changes
// the reader's focus - playback, a timestamp click, a word double-click - goes
// through here to keep the target visible on screen.
function setFocusedSentence(sentenceIdx, { scroll = true } = {}) {
  state.currentSentenceIdx = sentenceIdx;
  highlightActiveLine(sentenceIdx, scroll);
}

// Pinning is what stops pollTick from dragging the highlight onto the next
// sentence, which is the whole reason TTS used to speak the wrong line.
function setPinned(on) {
  if (state.pinned === on) return;
  state.pinned = on;
  if (on) {
    if (state.ytPlayer) state.ytPlayer.pauseVideo();
    // Drop any armed auto-pause so it can't fire against a stale end time
    // once playback resumes somewhere else.
    state.autoPauseTargetEnd = null;
    state.autoPauseArmedIdx = null;
  }
  if (state.currentSentenceIdx !== null) {
    highlightActiveLine(state.currentSentenceIdx, false); // repaint the badge, don't scroll
  }
}

// ---- Languages ----
function renderVoiceToggle() {
  const prof = state.langs[state.lang];
  el.voiceToggle.innerHTML = "";
  if (!prof) return;
  const names = Object.keys(prof.voices || {});
  state.voice = prof.voices[names[0]] || null;
  for (const [label, voice] of Object.entries(prof.voices || {})) {
    const btn = document.createElement("button");
    btn.className = "voice-btn" + (voice === state.voice ? " active" : "");
    btn.dataset.voice = voice;
    btn.textContent = label;
    el.voiceToggle.appendChild(btn);
  }
}

// The reading is only offered for languages that have one, and hiding it is a
// class on the list rather than a re-render, so the toggle is instant.
function syncReadingToggle() {
  const prof = state.langs[state.lang];
  const has = !!(prof && prof.has_reading);
  el.readingToggle.classList.toggle("hidden", !has);
  el.readingToggle.classList.toggle("active", has && state.showReading);
  el.list.classList.toggle("no-reading", !state.showReading);
}

function setLanguage(code) {
  if (!code || !state.langs[code] || code === state.lang) {
    syncReadingToggle();
    return;
  }
  state.lang = code;
  el.langSelect.value = code;
  renderVoiceToggle();
  syncReadingToggle();
}

async function initLanguages() {
  const resp = await fetch("/api/languages").then((r) => r.json());
  state.langs = Object.fromEntries(resp.languages.map((p) => [p.code, p]));
  populateLangSelect();
  state.lang = state.langs[resp.default] ? resp.default : resp.languages[0].code;
  el.langSelect.value = state.lang;
  renderVoiceToggle();
  syncReadingToggle();
}

el.langSelect.addEventListener("change", () => setLanguage(el.langSelect.value));

el.readingToggle.addEventListener("click", () => {
  state.showReading = !state.showReading;
  syncReadingToggle();
});

// ---- Loading videos ----
// Grouped by language rather than filtered to the current one. Filtering would
// hide every video saved under another language until you happened to select
// it, and the list emptying on a language switch reads like data loss.
async function refreshHistory() {
  const videos = await fetch("/api/videos").then((r) => r.json());
  state.history = {};
  el.historySelect.innerHTML = `<option value="">${t("historyDefaultOption")}</option>`;

  const byLang = new Map();
  for (const v of videos) {
    state.history[v.video_id] = v;
    if (!byLang.has(v.lang)) byLang.set(v.lang, []);
    byLang.get(v.lang).push(v);
  }

  // Registered order first, so the groups don't reshuffle as videos are added.
  const order = Object.keys(state.langs).filter((c) => byLang.has(c));
  for (const code of byLang.keys()) if (!order.includes(code)) order.push(code);

  for (const code of order) {
    const group = document.createElement("optgroup");
    group.label = (state.langs[code] && langLabel(state.langs[code])) || code;
    for (const v of byLang.get(code)) {
      const opt = document.createElement("option");
      opt.value = v.video_id;
      opt.textContent = v.title;
      if (v.video_id === state.videoId) opt.selected = true;
      group.appendChild(opt);
    }
    el.historySelect.appendChild(group);
  }
}

async function loadVideo(urlOrId, lang = state.lang) {
  if (!urlOrId) return;
  setLanguage(lang);
  el.status.textContent = t("statusLoadingTranscript");
  el.status.classList.remove("hidden");
  el.list.innerHTML = "";

  let loadResp;
  try {
    loadResp = await fetch("/api/videos/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url_or_id: urlOrId, lang: state.lang }),
    }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail || "Failed to load video");
      return r.json();
    });
  } catch (err) {
    el.status.textContent = t("statusLoadError", { message: err.message });
    return;
  }

  state.videoId = loadResp.meta.video_id;
  if (loadResp.meta.lang) setLanguage(loadResp.meta.lang);
  state.raw = loadResp.raw;
  state.bookmarks = new Set();
  state.currentSentenceIdx = null;
  state.explainCache.clear();
  resetChat();
  closePopup();
  el.videoTitle.textContent = loadResp.meta.title;
  loadPlayer(state.videoId);
  refreshHistory();

  el.status.textContent = t("statusSegmenting");
  const modResp = await fetch(videoUrl("/transcript", { mode: "modified" })).then((r) => r.json());
  state.modified = modResp.sentences;
  buildChunkMap();

  const bookmarksResp = await fetch(videoUrl("/bookmarks")).then((r) => r.json());
  state.bookmarks = new Set(bookmarksResp);

  // Read-only: badges come back for free if this video was scored before, and
  // nothing is generated behind your back if it wasn't.
  const recResp = await fetch(videoUrl("/recommendations")).then((r) => r.json());
  state.recommendScores = recResp.scores;
  computeRecommended();

  el.status.classList.add("hidden");
  render();
}

// Units per second, from timings the segmenter already stores. Fast lines are
// the harder shadowing targets. A unit is a word or a character depending on
// the language, which is fine: this only ever ranks sentences against others
// from the same video, never across languages.
function speechRate(sentenceIdx) {
  const sentence = state.modified[sentenceIdx];
  if (!sentence) return 0;
  const span = sentence.end - sentence.start;
  const range = sentence.unit_range;
  if (span <= 0 || !range) return 0;
  return (range[1] - range[0] + 1) / span;
}

// Take the top ratio-share of sentences by score. Sentences the server ruled
// ineligible keep a score of 0 and can never fill a slot, so a transcript of
// mostly short back-channel lines just yields fewer than the full share.
//
// The model scores in coarse steps, so ties decide a lot of the set. Breaking
// them by speech rate rather than by index picks the harder line of two equals,
// and incidentally spreads the picks across the video instead of front-loading
// them the way ascending index does.
function computeRecommended() {
  state.recommendedSet = new Set();
  if (!state.recommendScores.length || !state.modified.length) return;

  const k = Math.ceil(state.recommendRatio * state.modified.length);
  const ranked = state.recommendScores
    .filter((entry) => entry.score > 0)
    .sort(
      (a, b) =>
        b.score - a.score ||
        speechRate(b.index) - speechRate(a.index) ||
        a.index - b.index
    )
    .slice(0, k);
  for (const entry of ranked) state.recommendedSet.add(entry.index);
}

// Re-segmenting renumbers sentences, so index-keyed state is dropped server-side too.
function resetSentenceState() {
  state.bookmarks = new Set();
  state.currentSentenceIdx = null;
  state.autoPauseTargetEnd = null;
  state.autoPauseArmedIdx = null;
  state.explainCache.clear();
  state.recommendScores = [];
  state.recommendedSet = new Set();
  closePopup();
}

function buildChunkMap() {
  state.chunkToSentence = new Array(state.raw.length).fill(0);
  state.modified.forEach((sentence, sIdx) => {
    const [start, end] = sentence.chunk_range;
    for (let i = start; i <= end; i++) state.chunkToSentence[i] = sIdx;
  });
}

// ---- Rendering ----
function render() {
  if (!state.videoId) return;
  // The lines the hover index pointed at are about to be destroyed.
  state.hoveredSentenceIdx = null;
  el.list.innerHTML = "";

  if (state.mode === "raw") {
    for (const chunk of state.raw) {
      el.list.appendChild(buildLine(chunk.index, chunk.start, chunk.text, state.chunkToSentence[chunk.index]));
    }
  } else {
    const sentences =
      state.mode === "bookmarks"
        ? state.modified.filter((s) => state.bookmarks.has(s.index))
        : state.modified;
    for (const sentence of sentences) {
      el.list.appendChild(
        buildLine(sentence.index, sentence.start, sentence.text, sentence.index, sentence)
      );
    }
    if (state.mode === "bookmarks" && sentences.length === 0) {
      el.status.textContent = t("noBookmarks");
      el.status.classList.remove("hidden");
    } else {
      el.status.classList.add("hidden");
    }
    // The rebuilt lines lost their classes; put the highlight (and the pin
    // badge, which would otherwise go invisible while still armed) back.
    if (state.currentSentenceIdx !== null) highlightActiveLine(state.currentSentenceIdx, false);
  }
}

function buildLine(lineIdx, start, text, ownerSentenceIdx, sentence = null) {
  const li = document.createElement("li");
  li.className = "sentence-line";
  li.dataset.sentenceIdx = ownerSentenceIdx;

  // Raw mode renders one line per caption chunk, several of which can belong to
  // the same sentence, so a badge there would repeat down the page.
  if (state.mode !== "raw" && state.recommendedSet.has(ownerSentenceIdx)) {
    li.classList.add("recommended");
    li.appendChild(buildRecBadge(ownerSentenceIdx));
  }

  const star = document.createElement("span");
  star.className = "bookmark-star" + (state.bookmarks.has(ownerSentenceIdx) ? " starred" : "");
  star.textContent = state.bookmarks.has(ownerSentenceIdx) ? "★" : "☆";
  star.addEventListener("click", () => toggleBookmark(ownerSentenceIdx, star));

  const ts = document.createElement("span");
  ts.className = "timestamp";
  ts.textContent = fmtTime(start);
  ts.addEventListener("click", () => onTimestampClick(start, ownerSentenceIdx));

  const textSpan = document.createElement("span");
  textSpan.className = "sentence-text";
  textSpan.innerHTML = renderWordsHtml(sentence, text);

  li.appendChild(star);
  li.appendChild(ts);
  li.appendChild(textSpan);
  return li;
}

function buildRecBadge(sentenceIdx) {
  const entry = state.recommendScores[sentenceIdx];
  const badge = document.createElement("span");
  badge.className = "rec-badge";
  badge.textContent = "🎯";
  const parts = [entry.tag, entry.reason].filter(Boolean);
  badge.title = parts.length ? `${parts.join(" · ")} (${entry.score})` : t("recBadgeWorth");
  return badge;
}

// ---- Playback actions ----
function onTimestampClick(start, sentenceIdx) {
  if (!state.ytPlayer) return;
  if (state.mode === "raw") {
    state.ytPlayer.seekTo(start, true);
    state.ytPlayer.playVideo();
    state.autoPauseTargetEnd = null;
    state.autoPauseArmedIdx = null;
    // Raw mode has no .active highlight, so just arm Alt+S / Alt+G.
    state.currentSentenceIdx = sentenceIdx;
  } else {
    playSentence(sentenceIdx);
  }
}

function playSentence(idx) {
  const sentence = state.modified[idx];
  if (!sentence || !state.ytPlayer) return;
  state.ytPlayer.seekTo(sentence.start, true);
  state.ytPlayer.playVideo();
  state.autoPauseTargetEnd = state.autoPauseEnabled ? sentence.end : null;
  state.autoPauseArmedIdx = idx;
  setFocusedSentence(idx);
}

function navigateSentence(delta) {
  if (state.mode !== "modified" || !state.modified.length) return;
  const base = state.currentSentenceIdx === null ? 0 : state.currentSentenceIdx;
  const next = delta === 0 ? base : Math.min(Math.max(base + delta, 0), state.modified.length - 1);
  playSentence(next);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (state.explain.open) {
      closePopup();
      return;
    }
    // The chat textarea otherwise swallows every shortcut; let Esc hand the
    // keyboard back to the transcript before it starts pinning.
    const focusedTag = document.activeElement.tagName;
    if (focusedTag === "INPUT" || focusedTag === "SELECT" || focusedTag === "TEXTAREA") {
      document.activeElement.blur();
      return;
    }
    if (state.pinned) {
      setPinned(false);
      if (state.ytPlayer) state.ytPlayer.playVideo();
    } else if (state.currentSentenceIdx !== null) {
      setPinned(true);
    }
    return;
  }

  const tag = document.activeElement.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  if (e.ctrlKey || e.metaKey) return;

  // Sentence-level explanations work in every transcript mode, because a line's
  // data-sentence-idx resolves to a modified-transcript sentence even in raw mode.
  // Bare E / G are the reliable bindings: with no text input focused, no browser
  // shortcut claims a plain letter. The Alt combos are kept as aliases, but Chrome
  // swallows some of them before the page ever sees the event.
  const key = e.key.toLowerCase();
  const explainMode =
    key === "e" || (e.altKey && key === "s")
      ? "sentence"
      : key === "g"
      ? "grammar"
      : null;
  if (explainMode) {
    if (e.altKey) e.preventDefault(); // stop Alt+letter reaching the browser menu bar
    openExplain(explainMode);
    return;
  }

  // Like E and G, R works with the popup open - it speaks the same sentence
  // the popup is explaining.
  if (key === "r" && !e.altKey) {
    speakTargetSentence();
    return;
  }

  // T speaks whatever is selected, anywhere: a phrase inside a longer line, a
  // fragment of a chat answer, or - the main case - the example sentence in the
  // open popup. That last one is why this sits above the popup guard below.
  // Ctrl+T never reaches here, so opening a browser tab still works.
  if (key === "t" && !e.altKey) {
    speakSelection(e.shiftKey);
    return;
  }

  // O breaks a passage down part by part. Like T it reads the selection, so it
  // sits above the popup guard too: selecting a phrase inside an open breakdown
  // and pressing O again drills into that phrase.
  //
  // selectionText() is read HERE rather than inside openExplain, because
  // showExplainShell empties the popup body -- a selection made inside the
  // popup would be gone by the time the request was built.
  if (key === "o") {
    if (e.altKey) e.preventDefault(); // stop Alt+letter reaching the browser menu bar
    openExplain("breakdown", { text: selectionText() });
    return;
  }

  // Bare A/S/D only. Without this guard Alt+S would also scrub the video.
  if (e.altKey) return;
  if (state.explain.open) return;
  if (state.mode !== "modified") return;
  if (e.key === "a" || e.key === "A") navigateSentence(-1);
  else if (e.key === "s" || e.key === "S") navigateSentence(0);
  else if (e.key === "d" || e.key === "D") navigateSentence(1);
});

// ---- Bookmarks ----
async function toggleBookmark(sentenceIdx, starEl) {
  const starred = state.bookmarks.has(sentenceIdx);
  if (starred) {
    await fetch(videoUrl(`/bookmarks/${sentenceIdx}`), { method: "DELETE" });
    state.bookmarks.delete(sentenceIdx);
  } else {
    await fetch(videoUrl("/bookmarks"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sentence_idx: sentenceIdx }),
    });
    state.bookmarks.add(sentenceIdx);
  }
  render();
}

// ---- Explanation popup ----
// Double-click explains the word; Alt+S and Alt+G explain the whole sentence.
const EXPLAIN_MODES = {
  word: { labelKey: "explainModeWord", path: "word" },
  sentence: { labelKey: "explainModeSentence", path: "sentence" },
  grammar: { labelKey: "explainModeGrammar", path: "grammar" },
  breakdown: { labelKey: "explainModeBreakdown", path: "breakdown" },
};

el.list.addEventListener("dblclick", (e) => {
  const wordEl = e.target.closest(".word");
  if (!wordEl) return;
  const line = e.target.closest(".sentence-line");
  const sentenceIdx = parseInt(line.dataset.sentenceIdx, 10);
  // Don't scroll: the line is already under the cursor.
  setFocusedSentence(sentenceIdx, { scroll: false });
  openExplain("word", { word: wordEl.dataset.word, sentenceIdx });
});

el.list.addEventListener("mouseover", (e) => {
  const line = e.target.closest(".sentence-line");
  state.hoveredSentenceIdx = line ? parseInt(line.dataset.sentenceIdx, 10) : null;
});

el.list.addEventListener("mouseleave", () => {
  state.hoveredSentenceIdx = null;
});

// Point at the sentence you want explained. With the popup open the overlay
// swallows hover, so switching Alt+S <-> Alt+G stays on the sentence already
// shown instead of jumping to whatever is playing now.
function explainTargetIdx() {
  if (state.hoveredSentenceIdx !== null) return state.hoveredSentenceIdx;
  if (state.explain.open && state.explain.sentenceIdx !== null) return state.explain.sentenceIdx;
  return state.currentSentenceIdx;
}

function explainKey(mode, sentenceIdx, word, text) {
  if (mode === "word") return `word:${sentenceIdx}:${word.toLowerCase()}`;
  // A breakdown has no index of its own when it came from a selection, so the
  // selected text is what separates one from another inside the same line.
  if (mode === "breakdown") return `breakdown:${sentenceIdx}:${text}`;
  return `${mode}:${sentenceIdx}`;
}

async function openExplain(mode, { word = null, sentenceIdx = null, text = null } = {}) {
  if (!state.videoId) return;
  const idx = sentenceIdx === null ? explainTargetIdx() : sentenceIdx;
  if (idx === null || idx === undefined) {
    // Nothing to aim at - tell the reader how to pick a sentence.
    showExplainShell(mode, "", null);
    renderExplainError(t("explainPointHint"));
    return;
  }

  const sentence = state.modified[idx] ? state.modified[idx].text : "";
  // Selecting nothing means "break down the whole line", so the sentence stands
  // in for the selection from here on - including in the cache key, which is
  // what makes the no-selection breakdown a single shared entry.
  if (mode === "breakdown") text = text || sentence;
  state.explain = { open: false, mode, sentenceIdx: idx, word, text, seq: state.explain.seq + 1 };
  const seq = state.explain.seq;
  const title = mode === "word" ? word : mode === "breakdown" ? text : sentence;
  showExplainShell(mode, title, mode === "word" ? sentence : null);

  const key = explainKey(mode, idx, word || "", text || "");
  const cached = state.explainCache.get(key);
  if (cached) {
    renderExplain(mode, cached);
    return;
  }

  el.popupBody.innerHTML = `<div class="explain-loading">${t("explainLoading")}</div>`;

  const body =
    mode === "word"
      ? { word, sentence_idx: idx }
      : mode === "breakdown"
      ? { sentence_idx: idx, text }
      : { sentence_idx: idx };
  try {
    const resp = await fetch(videoUrl(`/explain/${EXPLAIN_MODES[mode].path}`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      let detail = t("requestFailed", { status: resp.status });
      try {
        detail = (await resp.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    const result = await resp.json();
    state.explainCache.set(key, result);
    if (seq !== state.explain.seq) return; // a newer request took over
    renderExplain(mode, result);
  } catch (err) {
    if (seq !== state.explain.seq) return;
    renderExplainError(err.message);
  }
}

function showExplainShell(mode, title, subtitle) {
  state.explain.open = true; // suppresses the A/S/D playback shortcuts
  el.popup.classList.remove("hidden");
  el.popupOverlay.classList.remove("hidden");
  el.popupMode.textContent = t(EXPLAIN_MODES[mode].labelKey);
  el.popupTitle.textContent = title;
  el.popupTitle.classList.toggle("is-sentence", mode !== "word");
  el.popupBody.innerHTML = "";

  const stale = el.popup.querySelector(".explain-quote");
  if (stale) stale.remove();
  if (subtitle) {
    // Word mode: show the sentence the word was clicked in, for context.
    const quote = document.createElement("div");
    quote.className = "explain-quote";
    quote.textContent = subtitle;
    el.popupTitle.after(quote);
  }
}

function explainSection(label, text) {
  const section = document.createElement("div");
  section.className = "explain-section";
  const heading = document.createElement("div");
  heading.className = "explain-label";
  heading.textContent = label;
  const value = document.createElement("div");
  value.className = "explain-value";
  value.textContent = text || "—";
  section.appendChild(heading);
  section.appendChild(value);
  return section;
}

function renderExplain(mode, result) {
  el.popupBody.innerHTML = "";

  if (mode === "word") {
    const head = document.createElement("div");
    head.className = "explain-wordhead";
    if (result.reading) {
      const reading = document.createElement("span");
      reading.className = "explain-reading";
      reading.textContent = result.reading;
      head.appendChild(reading);
    }
    if (result.part_of_speech) {
      const pos = document.createElement("span");
      pos.className = "explain-pos";
      pos.textContent = result.part_of_speech;
      head.appendChild(pos);
    }
    // A word on its own is the one thing the video's audio can never give you,
    // and for a tonal language it is exactly what you need to hear.
    head.appendChild(sayButton(result.word, t("sayHearWord")));
    el.popupBody.appendChild(head);

    el.popupBody.appendChild(explainSection(t("labelMeaningHere"), result.definition));

    const example = explainSection(t("labelExample"), result.example);
    example.querySelector(".explain-value").classList.add("is-example");
    example
      .querySelector(".explain-label")
      .appendChild(sayButton(result.example, t("sayHearExample")));
    // Non-English languages return the gloss as its own field, so the spoken
    // half stays pure. Shown under the sentence rather than inside it.
    if (result.example_english) {
      const gloss = document.createElement("div");
      gloss.className = "explain-gloss";
      gloss.textContent = result.example_english;
      example.appendChild(gloss);
    }
    el.popupBody.appendChild(example);
    return;
  }

  if (mode === "sentence") {
    // Labelled for both jobs it does: a simpler rewrite when the transcript is
    // English, a plain translation when it is not.
    const easy = explainSection(t("labelEasyEnglish"), result.easy_english);
    easy.classList.add("easy-english");
    el.popupBody.appendChild(easy);
    el.popupBody.appendChild(explainSection(t("labelWhatItMeans"), result.meaning));
    return;
  }

  if (mode === "breakdown") {
    renderBreakdown(result);
    return;
  }

  el.popupBody.appendChild(explainSection(t("labelStructure"), result.structure));
  el.popupBody.appendChild(explainSection(t("labelTense"), result.tense));
  if (result.points && result.points.length) {
    const section = document.createElement("div");
    section.className = "explain-section";
    const heading = document.createElement("div");
    heading.className = "explain-label";
    heading.textContent = t("labelGrammarPoints");
    section.appendChild(heading);
    for (const point of result.points) {
      const row = document.createElement("div");
      row.className = "grammar-point";
      const form = document.createElement("span");
      form.className = "grammar-form";
      form.textContent = point.form;
      const note = document.createElement("span");
      note.className = "grammar-note";
      note.textContent = point.note;
      row.appendChild(form);
      // The fragment is quoted from the sentence, so it is in the language
      // being learned even though the note beside it is in the explanation
      // language.
      if (point.form) row.appendChild(sayButton(point.form, t("sayHearForm")));
      row.appendChild(note);
      section.appendChild(row);
    }
    el.popupBody.appendChild(section);
  }
}

// ---- Breakdown ----
// Three stacked views of one analysis: what the passage says, how each part of
// it produced that meaning, and which words are worth keeping. The last of
// those is the only part meant to leave the app, which is what the Anki button
// is for.
function renderBreakdown(result) {
  // The whole passage, spoken as one clip, before it is taken apart.
  const head = document.createElement("div");
  head.className = "explain-wordhead";
  head.appendChild(sayButton(result.text, t("sayHearPassage")));
  el.popupBody.appendChild(head);

  // Labelled the way "In simple English" is: the server sends a translation for
  // a foreign transcript and a simpler rewrite for an English one, in whichever
  // explanation language is currently selected.
  const english = explainSection(t("labelInEnglish"), result.translation);
  english.classList.add("easy-english");
  el.popupBody.appendChild(english);

  if (result.parts && result.parts.length) {
    const section = document.createElement("div");
    section.className = "explain-section";
    const heading = document.createElement("div");
    heading.className = "explain-label";
    heading.textContent = t("labelPartByPart");
    section.appendChild(heading);
    for (const part of result.parts) {
      const row = document.createElement("div");
      row.className = "breakdown-row";
      const form = document.createElement("span");
      form.className = "breakdown-part";
      form.textContent = part.part;
      row.appendChild(form);
      // The part is quoted from the passage, so it is in the language being
      // learned; the gloss beside it is in the explanation language and gets
      // no button.
      row.appendChild(sayButton(part.part, t("sayHearPart")));
      const gloss = document.createElement("span");
      gloss.className = "breakdown-gloss";
      gloss.textContent = part.gloss;
      row.appendChild(gloss);
      section.appendChild(row);
    }
    el.popupBody.appendChild(section);
  }

  if (!result.vocab || !result.vocab.length) return;

  const section = document.createElement("div");
  section.className = "explain-section";
  const heading = document.createElement("div");
  heading.className = "explain-label vocab-heading";
  heading.textContent = t("labelVocab");

  const copy = document.createElement("button");
  copy.className = "anki-copy";
  copy.textContent = t("ankiCopyBtn");
  copy.title = t("ankiCopyTitle");
  copy.addEventListener("click", () => copyForAnki(result.vocab));
  heading.appendChild(copy);
  section.appendChild(heading);

  for (const entry of result.vocab) {
    const row = document.createElement("div");
    row.className = "vocab-row";

    const line = document.createElement("div");
    line.className = "vocab-line";
    // The dictionary form on its own is the thing the video audio never says -
    // it only ever speaks the inflected form - so it gets the button.
    line.appendChild(sayButton(entry.word, t("sayHearWord")));
    const word = document.createElement("span");
    word.className = "vocab-word";
    word.textContent = entry.word;
    line.appendChild(word);
    if (entry.reading) {
      const reading = document.createElement("span");
      reading.className = "vocab-reading";
      reading.textContent = entry.reading;
      line.appendChild(reading);
    }
    if (entry.part_of_speech) {
      const pos = document.createElement("span");
      pos.className = "explain-pos";
      pos.textContent = entry.part_of_speech;
      line.appendChild(pos);
    }
    const meaning = document.createElement("span");
    meaning.className = "vocab-meaning";
    meaning.textContent = entry.meaning;
    line.appendChild(meaning);
    row.appendChild(line);

    if (entry.explanation) {
      const explain = document.createElement("div");
      explain.className = "vocab-explain";
      explain.textContent = entry.explanation;
      row.appendChild(explain);
    }
    section.appendChild(row);
  }
  el.popupBody.appendChild(section);
}

// One card per line, three semicolon-separated fields, which is what Anki's
// text importer reads with the separator set to ";".
function ankiLines(vocab) {
  // A ';' inside a field would silently shift every later field one column to
  // the left, and a newline would split one card into two - so neither can be
  // allowed to survive into the text at all.
  const field = (s) => (s || "").replace(/;/g, ",").replace(/\s+/g, " ").trim();
  return vocab.map((v) => [field(v.word), field(v.meaning), field(v.explanation)].join(";"));
}

async function copyForAnki(vocab) {
  const text = ankiLines(vocab).join("\n");
  try {
    // 127.0.0.1 counts as a secure origin, so the async clipboard is available
    // here even over plain http.
    await navigator.clipboard.writeText(text);
  } catch (err) {
    // Some browsers still refuse it without a permission the app cannot ask
    // for. The old textarea trick has no such requirement.
    const scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "");
    scratch.style.position = "fixed";
    scratch.style.opacity = "0";
    document.body.appendChild(scratch);
    scratch.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      /* execCommand is gone in this browser */
    }
    scratch.remove();
    if (!ok) {
      toast(t("ankiCopyFail", { message: err.message }));
      return;
    }
  }
  const n = vocab.length;
  toast(t("ankiCopySuccess", { n }));
}

function renderExplainError(message) {
  el.popupBody.innerHTML = "";
  const error = document.createElement("div");
  error.className = "explain-error";
  error.textContent = message;
  el.popupBody.appendChild(error);
}

function closePopup() {
  state.explain.open = false;
  state.explain.seq += 1; // drop any response still in flight
  el.popup.classList.add("hidden");
  el.popupOverlay.classList.add("hidden");
}

el.popupClose.addEventListener("click", closePopup);
el.popupOverlay.addEventListener("click", closePopup);

// ---- TTS ----
// getVoices() is empty until Chrome has loaded them, so the first press would
// otherwise silently ignore the voice choice. Cache and refresh on the event.
let ttsVoices = window.speechSynthesis.getVoices();
window.speechSynthesis.addEventListener("voiceschanged", () => {
  ttsVoices = window.speechSynthesis.getVoices();
});

// Voices are named the way the speech service names them --
// "en-GB-SoniaNeural" -- so the locale is the first two segments.
function voiceLocale(voice) {
  const parts = (voice || "").split("-");
  return parts.length >= 2 ? `${parts[0]}-${parts[1]}` : "en-US";
}

// The locale a language should be spoken in, taken from its own first voice so
// there is no second place listing locales that could drift out of step.
function localeForLang(lang) {
  const prof = state.langs[lang];
  const first = prof && Object.values(prof.voices || {})[0];
  return first ? voiceLocale(first) : "en-US";
}

// Audio comes from the server, which can speak every language the app offers.
// The browser's own voices are kept only as a fallback for when that request
// fails, because what they cover depends entirely on the user's machine --
// here, Korean and US English and nothing else.
let ttsAudio = null;

function stopAudio() {
  if (ttsAudio) {
    ttsAudio.pause();
    URL.revokeObjectURL(ttsAudio.src);
    ttsAudio = null;
  }
  window.speechSynthesis.cancel();
}

// `voice` defaults to the current language's voice, but has to be overridable:
// speaking a selection in English while learning Japanese posts to ?lang=en,
// and the server rightly refuses a Japanese voice there. Passing null lets it
// fall back to the English profile's own default.
async function playFromServer(url, body, voice = state.voice) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, voice, slow: state.slowSpeech }),
  });
  if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || t("ttsFailedGeneric"));
  const blob = await resp.blob();
  stopAudio();
  ttsAudio = new Audio(URL.createObjectURL(blob));
  await ttsAudio.play();
}

// Same target rule as E / G: the line under the mouse wins, then the popup's
// line, then the playing (or pinned) line.
async function speakTargetSentence() {
  const idx = explainTargetIdx() ?? 0;
  const sentence = state.modified[idx];
  if (!sentence) return;
  try {
    await playFromServer(videoUrl("/tts"), { sentence_idx: idx });
  } catch (err) {
    console.warn("Server TTS failed, falling back to the browser:", err.message);
    speakText(sentence.text);
  }
}

// Any short piece of target-language text: a word, an example sentence, a
// grammar fragment. Cached server-side by its own text, so the same word looked
// up in a different video costs nothing the second time.
async function speakWord(text, { lang = state.lang, voice = state.voice } = {}) {
  if (!text) return;
  try {
    await playFromServer(`/api/tts/word?lang=${encodeURIComponent(lang)}`, { text }, voice);
  } catch (err) {
    // The browser fallback only covers languages the machine has voices for, so
    // say something rather than letting a silent no-op look like a dead key.
    console.warn("Server TTS failed, falling back to the browser:", err.message);
    toast(t("speakFailToast", { message: err.message }));
    // Fall back in the language that was asked for, not the one selected in the
    // toolbar -- otherwise Shift+T would retry English in a Japanese voice.
    speakText(text, voice ? voiceLocale(voice) : localeForLang(lang));
  }
}

// Matches tts.MAX_CHARS, so an over-long selection is refused here with a clear
// message instead of coming back as a 502.
const MAX_SPEAK_CHARS = 1000;

// T speaks the selection in the language being learned; Shift+T speaks it in
// English, for the definitions and chat answers that sit beside it.
function speakSelection(useEnglish) {
  const text = selectionText();
  if (!text) {
    toast(t("speakSelectEmpty"));
    return;
  }
  if (text.length > MAX_SPEAK_CHARS) {
    toast(t("speakTooLong", { n: text.length }));
    return;
  }
  // voice: null lets the server pick the English profile's own default, which
  // is required because state.voice belongs to the language being learned.
  speakWord(text, useEnglish ? { lang: "en", voice: null } : {});
}

// Attached only to text that really is in the language being learned. An
// English definition spoken by a Japanese voice is noise, so those fields get
// no button at all.
function sayButton(text, title = t("sayHearDefault")) {
  const btn = document.createElement("button");
  btn.className = "explain-say";
  btn.textContent = "🔊";
  btn.title = title;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    speakWord(text);
  });
  return btn;
}

function speakText(text, locale = voiceLocale(state.voice)) {
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = locale;
  const match =
    ttsVoices.find((v) => v.lang === locale) ||
    ttsVoices.find((v) => v.lang.replace("_", "-") === locale) ||
    ttsVoices.find((v) => v.lang.startsWith(locale.slice(0, 2)));
  if (match) utterance.voice = match;
  stopAudio();
  window.speechSynthesis.speak(utterance);
}

el.ttsBtn.addEventListener("click", speakTargetSentence);

el.slowToggle.addEventListener("click", () => {
  state.slowSpeech = !state.slowSpeech;
  el.slowToggle.classList.toggle("active", state.slowSpeech);
});

el.voiceToggle.addEventListener("click", (e) => {
  const btn = e.target.closest(".voice-btn");
  if (!btn) return;
  el.voiceToggle.querySelectorAll(".voice-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  state.voice = btn.dataset.voice;
});

el.autopauseToggle.addEventListener("change", () => {
  state.autoPauseEnabled = el.autopauseToggle.checked;
  state.autoPauseTargetEnd = null;
  state.autoPauseArmedIdx = null;
  syncStayToggle();
});

el.stayToggle.addEventListener("change", () => {
  state.stayOnSentence = el.stayToggle.checked;
});

// "Stay on sentence" only bites at the moment auto-pause stops playback, so it
// follows the auto-pause checkbox in and out of use.
function syncStayToggle() {
  el.stayToggle.disabled = !state.autoPauseEnabled;
  el.stayLabel.classList.toggle("disabled", !state.autoPauseEnabled);
}

// ---- Mode toggle ----
const MODE_LABEL_KEYS = { modified: "modeModified", raw: "modeOriginal" };

function setMode(mode) {
  state.mode = mode;
  if (mode !== "bookmarks") state.transcriptMode = mode;
  syncModeButtons();
  render();
}

function syncModeButtons() {
  const other = state.transcriptMode === "modified" ? "raw" : "modified";
  el.transcriptModeBtn.textContent = `⇄ ${t(MODE_LABEL_KEYS[state.transcriptMode])}`;
  el.transcriptModeBtn.title = t("modeSwitchTitle", { to: t(MODE_LABEL_KEYS[other]) });
  el.transcriptModeBtn.classList.toggle("active", state.mode !== "bookmarks");
  el.bookmarksModeBtn.classList.toggle("active", state.mode === "bookmarks");
}

el.transcriptModeBtn.addEventListener("click", () => {
  // From Bookmarks this button is a way back, not a flip.
  if (state.mode === "bookmarks") return setMode(state.transcriptMode);
  setMode(state.transcriptMode === "modified" ? "raw" : "modified");
});

el.bookmarksModeBtn.addEventListener("click", () => setMode("bookmarks"));

syncModeButtons();

// ---- Recommendations ----
el.recommendBtn.addEventListener("click", async () => {
  if (!state.videoId) return;
  el.recommendBtn.disabled = true;
  el.status.textContent = t("recommendScoring");
  el.status.classList.remove("hidden");
  try {
    const resp = await fetch(videoUrl("/recommendations"), { method: "POST" });
    if (!resp.ok) {
      let detail = t("requestFailed", { status: resp.status });
      try {
        detail = (await resp.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    const result = await resp.json();
    state.recommendScores = result.scores;
    computeRecommended();
    el.status.classList.add("hidden");
    render();
  } catch (err) {
    el.status.textContent = t("recommendFail", { message: err.message });
  } finally {
    el.recommendBtn.disabled = false;
  }
});

// Re-slicing an existing scoring pass, so no network call.
el.recommendRatio.addEventListener("change", () => {
  state.recommendRatio = parseFloat(el.recommendRatio.value);
  computeRecommended();
  render();
});

// ---- Cache controls ----
el.regenModifiedBtn.addEventListener("click", async () => {
  if (!state.videoId) return;
  el.status.textContent = t("regenModifiedStatus");
  el.status.classList.remove("hidden");
  const resp = await fetch(videoUrl("/regenerate", { target: "modified" }), { method: "POST" }).then((r) => r.json());
  state.modified = resp.sentences;
  buildChunkMap();
  resetSentenceState();
  el.status.classList.add("hidden");
  render();
});

el.regenRawBtn.addEventListener("click", async () => {
  if (!state.videoId) return;
  el.status.textContent = t("regenRawStatus");
  el.status.classList.remove("hidden");
  const resp = await fetch(videoUrl("/regenerate", { target: "raw" }), { method: "POST" }).then((r) => r.json());
  state.raw = resp.sentences;
  const modResp = await fetch(videoUrl("/transcript", { mode: "modified" })).then((r) => r.json());
  state.modified = modResp.sentences;
  buildChunkMap();
  resetSentenceState();
  el.status.classList.add("hidden");
  render();
});

el.flushDictBtn.addEventListener("click", async () => {
  if (!state.videoId) return;
  await fetch(videoUrl("/explain/flush"), { method: "POST" });
  state.explainCache.clear();
});

// ---- Header controls ----
el.loadBtn.addEventListener("click", () => loadVideo(el.input.value.trim()));
el.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadVideo(el.input.value.trim());
});
// The video's own language wins over whatever the selector happens to show.
// Without this a video saved under one language could be reopened under
// another, and every explanation for it would be generated from the wrong
// prompt against text it doesn't match.
el.historySelect.addEventListener("change", () => {
  const videoId = el.historySelect.value;
  if (!videoId) return;
  const meta = state.history[videoId];
  loadVideo(videoId, (meta && meta.lang) || state.lang);
});

// ---- Chat: markdown ----
// The model's answer is built from YouTube captions nobody here controls, so a
// caption carrying markup is a real path into this DOM. Everything is escaped
// once, up front, before a single tag is inserted -- which makes injection
// structurally impossible rather than merely unlikely. Nothing below may ever
// reintroduce raw model text into the HTML string.
//
// Returns { html, diagrams }: diagram source is handed back out-of-band rather
// than embedded in a data- attribute, because escapeHtml() leaves quotes alone
// (textContent -> innerHTML escapes & < > only) and mermaid labels are quoted
// often enough that an attribute would break in ordinary use, not just under
// attack.
function renderMarkdown(text) {
  let html = escapeHtml(text);

  // Mermaid blocks are pulled out first and parked as placeholders, so the
  // inline rules below can't mangle the diagram source.
  //
  // The source is un-escaped on the way out. Mermaid is a text parser, not an
  // HTML one: an arrow that reached it as "--&gt;" is a syntax error, which is
  // why every diagram failed to draw. This does not weaken the escape-once rule
  // above, because the un-escaped text never returns to the HTML string - it
  // goes only to mermaid.render(), whose securityLevel:"strict" sanitises the
  // SVG it produces, and to showDiagramSource(), which writes textContent.
  const diagrams = [];
  html = html.replace(/```mermaid\n([\s\S]*?)```/g, (_, src) => {
    diagrams.push(unescapeHtml(src.trim()));
    return `<!--M${diagrams.length - 1}-->`;
  });

  const blocks = [];
  html = html.replace(/```[a-z]*\n?([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `<!--B${blocks.length - 1}-->`;
  });

  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/^#{1,6}\s+(.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  // Runs of list items become one list; anything else passes through.
  html = html.replace(/(?:^[ \t]*[-*]\s+.+$\n?)+/gm, (run) => {
    const items = run.trim().split("\n").map((l) => `<li>${l.replace(/^[ \t]*[-*]\s+/, "")}</li>`);
    return `<ul>${items.join("")}</ul>\n`;
  });
  html = html.replace(/(?:^[ \t]*\d+\.\s+.+$\n?)+/gm, (run) => {
    const items = run.trim().split("\n").map((l) => `<li>${l.replace(/^[ \t]*\d+\.\s+/, "")}</li>`);
    return `<ol>${items.join("")}</ol>\n`;
  });

  // [mm:ss] -> a seek link. data-t carries seconds so the handler needs no parsing.
  html = html.replace(/\[(\d{1,3}):([0-5]\d)\]/g, (m, mins, secs) => {
    const t = Number(mins) * 60 + Number(secs);
    return `<a class="ts" data-t="${t}" title="Jump to ${m.slice(1, -1)}">${m}</a>`;
  });

  html = html
    .split(/\n{2,}/)
    .map((para) => {
      const trimmed = para.trim();
      if (!trimmed) return "";
      // Block-level output is already wrapped; only bare text needs a <p>.
      if (/^(<h4|<ul|<ol|<pre|<!--)/.test(trimmed)) return trimmed;
      return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
    })
    .join("");

  html = html.replace(/<!--B(\d+)-->/g, (_, i) => blocks[Number(i)]);
  // Only an index goes into the markup; the source travels beside it.
  html = html.replace(
    /<!--M(\d+)-->/g,
    (_, i) => `<div class="mermaid-box" data-idx="${Number(i)}"></div>`
  );
  return { html, diagrams };
}

// ---- Chat: mermaid, loaded on demand ----
// 2-3MB of library that most conversations never need, so it is fetched the
// first time a diagram actually appears and never again. The module-level
// promise is what guarantees "never again", including for concurrent replies.
let mermaidPromise = null;

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js";
      script.onload = () => {
        mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
        resolve();
      };
      script.onerror = () => {
        // Cleared so a later attempt can retry rather than reject forever.
        mermaidPromise = null;
        reject(new Error(t("diagramLoadFail")));
      };
      document.head.appendChild(script);
    });
  }
  return mermaidPromise;
}

let mermaidCounter = 0;

async function renderDiagrams(container, diagrams) {
  const boxes = container.querySelectorAll(".mermaid-box");
  if (!boxes.length) return;

  const srcOf = (box) => diagrams[Number(box.dataset.idx)] || "";

  try {
    await loadMermaid();
  } catch (err) {
    boxes.forEach((box) => showDiagramSource(box, err.message, srcOf(box)));
    return;
  }

  for (const box of boxes) {
    try {
      const { svg } = await mermaid.render(`mmd-${mermaidCounter++}`, srcOf(box));
      box.innerHTML = svg;
    } catch (_) {
      // Model-written mermaid is not always valid. Falling back to the source
      // beats an empty box, and must never throw out of the reply render.
      showDiagramSource(box, t("diagramDrawFail"), srcOf(box));
    }
    scrollChatToBottom();
  }
}

// textContent throughout: the source is never parsed as HTML on this path.
function showDiagramSource(box, message, src) {
  box.textContent = "";
  const note = document.createElement("div");
  note.style.color = "#e58080";
  note.style.marginBottom = "6px";
  note.style.fontSize = "0.9em";
  note.textContent = message;
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = src;
  pre.appendChild(code);
  box.append(note, pre);
}

// ---- Chat: rendering ----
function scrollChatToBottom() {
  el.chatLog.scrollTop = el.chatLog.scrollHeight;
}

// Returns { node, diagrams } so the caller can draw any mermaid blocks once the
// bubble is in the DOM. User text and errors stay on the textContent path.
function appendChatMessage(role, content, isError) {
  const div = document.createElement("div");
  div.className = `chat-msg ${isError ? "error" : role}`;
  let diagrams = [];
  if (role === "user" || isError) {
    div.textContent = content;
  } else {
    const rendered = renderMarkdown(content);
    div.innerHTML = rendered.html;
    diagrams = rendered.diagrams;
  }
  el.chatLog.appendChild(div);
  scrollChatToBottom();
  return { node: div, diagrams };
}

function renderChatEmptyState() {
  el.chatLog.textContent = "";
  const hint = document.createElement("div");
  hint.id = "chat-empty";
  hint.textContent = state.videoId ? t("chatEmptyWithVideo") : t("chatEmptyNoVideo");
  el.chatLog.appendChild(hint);
}

function resetChat() {
  // Bumping seq is what makes Clear safe mid-request: an in-flight reply for
  // the old conversation will find its seq stale and drop itself.
  state.chat.messages = [];
  state.chat.seq++;
  state.chat.busy = false;
  el.chatMeta.textContent = "";
  el.chatMeta.classList.remove("warn");
  renderChatEmptyState();
  setChatEnabled(true);
}

function setChatEnabled(enabled) {
  el.chatSend.disabled = !enabled;
  el.chatInput.disabled = !enabled;
  el.chatChips.querySelectorAll(".chat-chip").forEach((chip) => {
    chip.disabled = !enabled;
  });
}

// ---- Chat: sending ----
const CHIP_PROMPT_KEYS = {
  summarize: "chipSummarizePrompt",
  quiz: "chipQuizPrompt",
  flowchart: "chipFlowchartPrompt",
};

async function sendChat(text) {
  const question = text.trim();
  if (!question || state.chat.busy) return;
  if (!state.videoId) {
    el.chatMeta.textContent = t("chatNeedVideo");
    el.chatMeta.classList.add("warn");
    return;
  }

  const empty = document.getElementById("chat-empty");
  if (empty) empty.remove();

  state.chat.messages.push({ role: "user", content: question });
  appendChatMessage("user", question, false);

  const seq = ++state.chat.seq;
  state.chat.busy = true;
  setChatEnabled(false);

  const typing = document.createElement("div");
  typing.className = "chat-msg assistant typing";
  typing.innerHTML = "<span></span><span></span><span></span>";
  el.chatLog.appendChild(typing);
  scrollChatToBottom();

  try {
    const resp = await fetch(videoUrl("/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.chat.messages }),
    });
    if (!resp.ok) {
      let detail = t("requestFailed", { status: resp.status });
      try {
        detail = (await resp.json()).detail || detail;
      } catch (_) {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    const result = await resp.json();
    if (seq !== state.chat.seq) return; // a newer question, or Clear, took over

    typing.remove();
    state.chat.messages.push({ role: "assistant", content: result.reply });
    const bubble = appendChatMessage("assistant", result.reply, false);
    updateChatMeta(result);
    // Fire-and-forget: diagram failures are handled inside and must never
    // knock out an answer that already rendered fine.
    renderDiagrams(bubble.node, bubble.diagrams).catch(() => {});
  } catch (err) {
    if (seq !== state.chat.seq) return;
    typing.remove();
    appendChatMessage("assistant", err.message, true);
  } finally {
    if (seq === state.chat.seq) {
      state.chat.busy = false;
      setChatEnabled(true);
      el.chatInput.focus();
    }
  }
}

function updateChatMeta(result) {
  // Cost is shown because "cheap" was the whole point: at gpt-4o-mini's
  // $0.15/1M input, a 30k-token transcript is well under a cent per message.
  const tokens = result.context_tokens || 0;
  const cost = ((tokens / 1e6) * 0.15).toFixed(3);
  let text = t("chatTokensCost", { k: Math.round(tokens / 1000), cost });
  if (result.truncated) {
    text = t("chatTruncatedWarn", { time: fmtTime(result.covered_until), rest: text });
  }
  el.chatMeta.textContent = text;
  el.chatMeta.classList.toggle("warn", !!result.truncated);
}

// ---- Chat: wiring ----
el.chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = el.chatInput.value;
  el.chatInput.value = "";
  sendChat(text);
});

el.chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = el.chatInput.value;
    el.chatInput.value = "";
    sendChat(text);
  }
});

el.chatChips.addEventListener("click", (e) => {
  const chip = e.target.closest(".chat-chip");
  if (chip) sendChat(t(CHIP_PROMPT_KEYS[chip.dataset.prompt]) || "");
});

el.chatClear.addEventListener("click", resetChat);

// A citation click seeks the player, the same call playSentence() makes.
el.chatLog.addEventListener("click", (e) => {
  const link = e.target.closest("a.ts");
  if (!link || !state.ytPlayer) return;
  state.ytPlayer.seekTo(Number(link.dataset.t), true);
  state.ytPlayer.playVideo();
});

renderChatEmptyState();

state.autoPauseEnabled = el.autopauseToggle.checked;
state.stayOnSentence = el.stayToggle.checked;
syncStayToggle();
state.recommendRatio = parseFloat(el.recommendRatio.value);
// Before languages: the picker's labels themselves depend on it, and
// populateLangSelect() below needs uiLang set correctly on the first pass.
loadUiLang();
// Languages first: the history dropdown groups by them, and the load flow
// needs a language before it can ask for anything.
initLanguages().then(refreshHistory);

// ---- Resizable panes ----
// Two dividers, both pointer-driven. The vertical one stores the left column as
// a percentage of #app-main, so the split keeps its proportion across window
// resizes; the horizontal one stores the video pane height in pixels and is
// re-clamped against the measured layout on every move, which is what keeps the
// chat panel from being squeezed out of existence.
const LAYOUT_KEY = "shadowing.layout.v1";
const MIN_COL_PCT = 18;
const MAX_COL_PCT = 82;
const MIN_VIDEO_H = 120;
const MIN_CHAT_H = 150;
const DEFAULT_COL_PCT = 50;
// Matches the min-width/max-width the popup's own rule sets, so a width read
// back out of storage is clamped to the same range the browser would allow.
const MIN_POPUP_W = 320;
const MAX_POPUP_VW = 0.95;

const layoutEl = {
  main: document.getElementById("app-main"),
  transcript: document.getElementById("transcript-panel"),
  playerPanel: document.getElementById("player-panel"),
  stage: document.getElementById("video-stage"),
  chat: document.getElementById("chat-panel"),
  colResizer: document.getElementById("col-resizer"),
  rowResizer: document.getElementById("row-resizer"),
};

// videoH and popupW stay null until the user actually drags them, so the
// defaults keep tracking the window (a 16:9 pane, a popup sized to the viewport)
// instead of freezing at load time.
const layout = { leftPct: DEFAULT_COL_PCT, videoH: null, popupW: null };

function loadLayout() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
  } catch {
    saved = null;
  }
  if (saved && typeof saved === "object") {
    if (Number.isFinite(saved.leftPct)) layout.leftPct = saved.leftPct;
    if (Number.isFinite(saved.videoH)) layout.videoH = saved.videoH;
    if (Number.isFinite(saved.popupW)) layout.popupW = saved.popupW;
  }
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch {
    /* private mode / quota - the dividers still work, they just don't persist */
  }
}

function defaultVideoH() {
  const w = layoutEl.stage.clientWidth || layoutEl.playerPanel.clientWidth;
  return Math.round((w * 9) / 16);
}

function applyLeftPct(pct) {
  layout.leftPct = Math.min(MAX_COL_PCT, Math.max(MIN_COL_PCT, pct));
  document.documentElement.style.setProperty("--left-w", `${layout.leftPct.toFixed(3)}%`);
}

// Clamped against what the chat panel can still give up, measured live, so the
// limit is correct whatever else is in the column (a wrapped controls row, the
// shortcuts box open or shut).
function applyVideoH(px) {
  // Negative slack (a window too short for both) is deliberately not floored at
  // zero: it pulls the ceiling below the current height, so re-applying on
  // resize shrinks the video instead of crushing the chat.
  const slack = layoutEl.chat.offsetHeight - MIN_CHAT_H;
  const max = Math.max(MIN_VIDEO_H, layoutEl.stage.offsetHeight + slack);
  const h = Math.round(Math.min(max, Math.max(MIN_VIDEO_H, px)));
  document.documentElement.style.setProperty("--video-h", `${h}px`);
  return h;
}

// The popup is resized with the browser's own corner handle rather than a
// divider of ours, because it has no neighbour to trade space with -- it floats
// over everything. That leaves no pointerdown/pointerup pair to hang a save on,
// so the width is observed instead of dragged.
function applyPopupWidth() {
  if (layout.popupW === null) return;
  const max = Math.round(window.innerWidth * MAX_POPUP_VW);
  el.popup.style.width = `${Math.min(max, Math.max(MIN_POPUP_W, layout.popupW))}px`;
}

let popupWidthSaveHandle = null;

// A user drag is the only thing that writes an inline width -- the default
// comes from CSS - so the presence of one is what separates a real resize from
// the popup merely being shown, or reflowing as its content loads.
const popupResizeObserver = new ResizeObserver(() => {
  const inline = parseFloat(el.popup.style.width);
  if (!Number.isFinite(inline) || inline === layout.popupW) return;
  layout.popupW = inline;
  clearTimeout(popupWidthSaveHandle);
  popupWidthSaveHandle = setTimeout(saveLayout, 250);
});

popupResizeObserver.observe(el.popup);

function applyLayout() {
  applyLeftPct(layout.leftPct);
  const h = applyVideoH(layout.videoH === null ? defaultVideoH() : layout.videoH);
  if (layout.videoH !== null) layout.videoH = h;
  applyPopupWidth();
}

function startResize(e, axis) {
  const resizer = axis === "x" ? layoutEl.colResizer : layoutEl.rowResizer;
  e.preventDefault();
  // Pointer capture is what makes dragging across the YouTube iframe work: the
  // moves keep coming to the divider instead of being swallowed by the frame.
  resizer.setPointerCapture(e.pointerId);
  resizer.classList.add("dragging");
  document.body.classList.add(axis === "x" ? "resizing-x" : "resizing-y");

  const startX = e.clientX;
  const startY = e.clientY;
  const startPct = layout.leftPct;
  const startH = layoutEl.stage.offsetHeight;
  const mainW = layoutEl.main.clientWidth || 1;

  const onMove = (ev) => {
    if (axis === "x") applyLeftPct(startPct + ((ev.clientX - startX) / mainW) * 100);
    else layout.videoH = applyVideoH(startH + (ev.clientY - startY));
  };

  const onEnd = () => {
    resizer.removeEventListener("pointermove", onMove);
    resizer.removeEventListener("pointerup", onEnd);
    resizer.removeEventListener("pointercancel", onEnd);
    resizer.classList.remove("dragging");
    document.body.classList.remove("resizing-x", "resizing-y");
    saveLayout();
  };

  resizer.addEventListener("pointermove", onMove);
  resizer.addEventListener("pointerup", onEnd);
  resizer.addEventListener("pointercancel", onEnd);
}

layoutEl.colResizer.addEventListener("pointerdown", (e) => startResize(e, "x"));
layoutEl.rowResizer.addEventListener("pointerdown", (e) => startResize(e, "y"));

// Double-click a divider to go back to the default: an even split, or a 16:9
// video pane.
layoutEl.colResizer.addEventListener("dblclick", () => {
  applyLeftPct(DEFAULT_COL_PCT);
  saveLayout();
});

layoutEl.rowResizer.addEventListener("dblclick", () => {
  layout.videoH = null;
  applyVideoH(defaultVideoH());
  saveLayout();
});

// Keyboard nudging for a focused divider. Arrows only - the transcript
// shortcuts are all letters, so nothing collides.
layoutEl.colResizer.addEventListener("keydown", (e) => {
  const step = e.shiftKey ? 5 : 1;
  if (e.key === "ArrowLeft") applyLeftPct(layout.leftPct - step);
  else if (e.key === "ArrowRight") applyLeftPct(layout.leftPct + step);
  else return;
  e.preventDefault();
  saveLayout();
});

layoutEl.rowResizer.addEventListener("keydown", (e) => {
  const step = e.shiftKey ? 48 : 16;
  const current = layoutEl.stage.offsetHeight;
  if (e.key === "ArrowUp") layout.videoH = applyVideoH(current - step);
  else if (e.key === "ArrowDown") layout.videoH = applyVideoH(current + step);
  else return;
  e.preventDefault();
  saveLayout();
});

// A smaller window can invalidate a stored height; re-applying runs it back
// through the clamp.
window.addEventListener("resize", applyLayout);

loadLayout();
applyLayout();

// ---- Theme ----
// Light is the default so the app opens bright; a returning visitor's last
// choice is remembered the same way the pane layout is.
const THEME_KEY = "shadowing.theme.v1";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  el.themeToggle.textContent = theme === "dark" ? t("themeToggleLight") : t("themeToggleDark");
}

function loadTheme() {
  let theme = "light";
  try {
    theme = localStorage.getItem(THEME_KEY) || "light";
  } catch {
    theme = "light";
  }
  applyTheme(theme);
}

el.themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* private mode / quota - the toggle still works, it just won't persist */
  }
});

loadTheme();
