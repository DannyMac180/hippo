import glob, json, os, sys, time
mode, mem, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
notes = glob.glob(os.path.join(mem, "daily", "handoff-test-*.md"))
text = open(notes[0]).read() if notes else ""
name = os.path.basename(notes[0]) if notes else ""
if mode == "record":
    json.dump({"prompt": prompt, "cwd": os.getcwd(), "listing": os.listdir("."), "note": text, "name": name}, open(sys.argv[4], "w"))
    mode = "correct"
if mode == "correct": print(text); print("Recorded in daily/" + name)
elif mode == "wrong": print("I could not find any decision.")
elif mode == "decision_only": print("\n".join(l for l in text.splitlines() if l.startswith("Decision:")))
elif mode == "no_filename": print(text)
elif mode == "hang": time.sleep(40)
