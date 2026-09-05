import React, { useEffect, useRef, useState } from "react"

const labels = { recording: "録音中", stopping: "保存中", recorded: "録音保存済み", transcribing: "文字起こし中", completed: "完了", interrupted: "中断", failed: "エラー" }
const languages = [["ja", "日本語"], ["en", "英語"], ["ar", "アラビア語"], ["es", "スペイン語"], ["zh", "中国語"], ["ko", "韓国語"]]
const busyStates = new Set(["recording", "stopping", "transcribing"])
const duration = (seconds = 0) => `${Math.floor(seconds / 3600).toString().padStart(2, "0")}:${Math.floor(seconds / 60 % 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`

async function api(path = "", body) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    const response = await fetch(`/api/meetings${path}`, { signal: controller.signal, ...(body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || "操作に失敗しました。")
    return data
  } finally {
    clearTimeout(timeout)
  }
}

export default function MeetingApp() {
  const [meetings, setMeetings] = useState([])
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [status, setStatus] = useState(null)
  const [title, setTitle] = useState("")
  const [language, setLanguage] = useState("ja")
  const [error, setError] = useState("")
  const [connectionError, setConnectionError] = useState("")
  const [pending, setPending] = useState("")
  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const selection = useRef(selected)
  selection.current = selected

  useEffect(() => {
    let alive = true
    let timer
    async function refresh() {
      const current = selection.current
      try {
        const [list, health] = await Promise.all([api(), api("/status")])
        const exists = list.meetings.some(m => m.id === current)
        const record = exists ? await api(`/${current}`) : null
        if (!alive) return
        setMeetings(list.meetings)
        setStatus(health)
        setConnectionError("")
        if (current === selection.current) setDetail(record)
        if (current && !exists) setSelected(null)
        if (!selection.current && list.meetings.length) {
          setSelected(list.meetings.find(m => busyStates.has(m.status))?.id || list.meetings[0].id)
        }
      } catch (exception) {
        if (alive) setConnectionError(`接続を確認しています。${exception.message} 録音状況は再接続後に確認してください。`)
      } finally {
        if (alive) timer = setTimeout(refresh, 1000)
      }
    }
    refresh()
    return () => { alive = false; clearTimeout(timer) }
  }, [selected])

  async function action(path, body = {}) {
    setPending(path || "start")
    setError("")
    try {
      const record = await api(path, body)
      if (record.deleted) {
        setMeetings(items => items.filter(m => m.id !== record.deleted))
        setSelected(null)
        setDetail(null)
        setDeleteConfirm(false)
      } else {
        setSelected(record.id)
        setDetail(record)
        setMeetings(items => [record, ...items.filter(m => m.id !== record.id)])
      }
    } catch (exception) {
      setError(exception.message)
    } finally {
      setPending("")
    }
  }

  const active = meetings.find(m => busyStates.has(m.status))
  const disabled = pending || !!connectionError
  const visibleError = error || (detail?.id === selected ? detail.error : "")
  const translatorUrl = new URL(window.location.href)
  translatorUrl.protocol = "http:"
  translatorUrl.port = "3000"
  translatorUrl.pathname = "/"
  translatorUrl.search = ""

  return <main className="meeting-app">
    <header className="meeting-header">
      <div><span className="eyebrow">LOCAL AI RECORDER</span><h1>会議録音</h1></div>
      <a href={translatorUrl.href}>翻訳モード ↗</a>
    </header>
    <p className="intro">Pi のマイクで録音し、停止後に文字起こしします。</p>
    {connectionError && <p className="alert" role="status">{connectionError}</p>}
    {visibleError && <p className="alert" role="alert">{visibleError}</p>}
    {status?.warnings?.map(w => <p key={w} className="alert">{w}</p>)}
    {status && !status.recording_available && <p className="alert">録音環境が未準備です。Pi で会議用のセットアップを行ってください。</p>}
    {status && !status.transcription_available && <p className="alert">文字起こし環境が未準備です。録音済みの音声は保持されます。{status.errors.join(" ")}</p>}

    <section aria-label="新しい会議" className="panel">
      <div className="fields">
        <label>会議名<input maxLength={120} placeholder="例：週次ミーティング" value={title} disabled={!!active || pending} onChange={e => setTitle(e.target.value)} /></label>
        <label>話す言語<select value={language} disabled={!!active || pending} onChange={e => setLanguage(e.target.value)}>{languages.map(([code, name]) => <option value={code} key={code}>{name}</option>)}</select></label>
      </div>
      {active ? <div className="recording-bar">
        <button className="active-meeting" onClick={() => { setSelected(active.id); setDetail(null); setDeleteConfirm(false) }}><span className={active.status === "recording" ? "live-dot" : "dot"} />{labels[active.status]} · {duration(active.duration_seconds)}</button>
        {active.status === "recording" && <button className="stop" disabled={disabled} onClick={() => action(`/${active.id}/stop`)}>録音を停止</button>}
        {active.status === "stopping" && <span>音声を保存しています…</span>}
      </div> : <button className="primary" disabled={disabled || !status?.recording_available} onClick={() => action("", { title, language })}>{pending === "start" ? "録音を開始しています…" : "● 録音を開始"}</button>}
      {status && <small>録音デバイス：{status.audio_device}</small>}
      <small>録音は Pi 側で続きます。終了時は「録音を停止」を押してください。</small>
    </section>

    <section className="results" aria-label="保存した会議">
      <div className="section-heading"><h2>保存した会議</h2><span>{meetings.length}件</span></div>
      {meetings.length === 0 ? <p className="empty">録音を開始すると、ここに会議が表示されます。</p> : <>
        <label className="meeting-selector">会議を選択<select value={selected || ""} onChange={e => { setSelected(e.target.value); setDetail(null); setDeleteConfirm(false) }}>
          {!selected && <option value="">選択してください</option>}
          {meetings.map(m => <option key={m.id} value={m.id}>{m.title} · {labels[m.status]}</option>)}
        </select></label>
        {detail && detail.id === selected && <article className="panel transcript-panel">
          <div className="section-heading"><h2>{detail.title}</h2><span className="badge">{labels[detail.status]}</span></div>
          <p className="meta">{new Date(detail.created_at).toLocaleString("ja-JP")} · {duration(detail.duration_seconds)}</p>
          {detail.notice && <p>{detail.notice}</p>}
          {detail.status === "transcribing" && <div role="status">
            <p>文字起こし中：{detail.progress.done} / {detail.progress.total} 区間</p>
            <progress max={detail.progress.total || 1} value={detail.progress.done} />
          </div>}
          <div className="actions">
            {!busyStates.has(detail.status) && <button className="primary" disabled={disabled || !!active || !status?.transcription_available || !detail.can_transcribe} onClick={() => action(`/${detail.id}/transcribe`)}>{detail.status === "completed" ? "文字起こしを再実行" : "文字起こしを開始・再開"}</button>}
            {detail.status === "transcribing" && <button disabled={disabled} onClick={() => action(`/${detail.id}/cancel`)}>文字起こしを中断</button>}
            {detail.has_audio && <a download href={`/api/meetings/${detail.id}/audio.wav`}>音声を保存</a>}
            {detail.transcript && <a download href={`/api/meetings/${detail.id}/transcript.md`}>文字起こしを保存</a>}
          </div>
          <div className="transcript" aria-label="文字起こし結果">
            {detail.transcript?.segments.length ? detail.transcript.segments.map(s => <p key={s.id}><time>{duration(s.start)}–{duration(s.end)}</time><span>{s.text}</span></p>) : <p className="empty">{detail.status === "completed" ? "音声から発話を認識できませんでした。" : "文字起こし結果はここに表示されます。"}</p>}
          </div>
          {!busyStates.has(detail.status) && <div className="delete-area">
            {deleteConfirm ? <><span>音声と文字起こしを削除します。</span><button className="stop" disabled={disabled} onClick={() => action(`/${detail.id}/delete`)}>削除する</button><button onClick={() => setDeleteConfirm(false)}>戻る</button></> : <button disabled={disabled} onClick={() => setDeleteConfirm(true)}>この会議を削除</button>}
          </div>}
        </article>}
      </>}
    </section>
    <footer>音声と文字起こしはこの端末に保存されます。</footer>
  </main>
}
