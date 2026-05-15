import { useRef, useState } from "react";

interface Props {
    email: File | null;
    pdf: File | null;
    onPick: (email: File | null, pdf: File | null) => void;
}

function classify(files: FileList | File[]): { email: File | null; pdf: File | null } {
    let email: File | null = null;
    let pdf: File | null = null;
    for (const f of Array.from(files)) {
        const lower = f.name.toLowerCase();
        if (lower.endsWith(".json")) email = f;
        else if (lower.endsWith(".pdf")) pdf = f;
    }
    return { email, pdf };
}

export function UploadZone({ email, pdf, onPick }: Props) {
    const [drag, setDrag] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    function merge(picked: { email: File | null; pdf: File | null }) {
        onPick(picked.email ?? email, picked.pdf ?? pdf);
    }

    return (
        <div>
            <div
                className={drag ? "upload-zone drag" : "upload-zone"}
                onClick={() => inputRef.current?.click()}
                onDragOver={(e) => {
                    e.preventDefault();
                    setDrag(true);
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setDrag(false);
                    merge(classify(e.dataTransfer.files));
                }}
            >
                <strong>Drop Email.json + Invoice.pdf</strong>
                <p>or click to choose files</p>
                <input
                    ref={inputRef}
                    type="file"
                    multiple
                    accept=".json,.pdf,application/json,application/pdf"
                    hidden
                    onChange={(e) => {
                        if (e.target.files) merge(classify(e.target.files));
                    }}
                />
            </div>
            {email && (
                <div className="file-row">
                    <span className="name">📧 {email.name}</span>
                    <button
                        className="btn ghost"
                        style={{ padding: "2px 8px", fontSize: 11 }}
                        onClick={() => onPick(null, pdf)}
                    >
                        clear
                    </button>
                </div>
            )}
            {pdf && (
                <div className="file-row">
                    <span className="name">📄 {pdf.name}</span>
                    <button
                        className="btn ghost"
                        style={{ padding: "2px 8px", fontSize: 11 }}
                        onClick={() => onPick(email, null)}
                    >
                        clear
                    </button>
                </div>
            )}
        </div>
    );
}
