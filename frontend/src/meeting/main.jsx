import React from "react"
import { createRoot } from "react-dom/client"
import MeetingApp from "./MeetingApp.jsx"
import "./meeting.css"

createRoot(document.getElementById("root")).render(
  <React.StrictMode><MeetingApp /></React.StrictMode>,
)
