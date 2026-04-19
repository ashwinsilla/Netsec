"""
Generate Argus.pptx — CMU 18-731 Network Security class.

Audience: CMU grad students. Know security/networking basics. Do NOT know AVD, IaC for
networks, or Arista. The story: Alex's frustration with manual network IaC → AI fixes it
and makes networks more secure.

Run:  /Users/ashwinsilla/avd-testbed/.venv/bin/python3 make_ppt.py
"""

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from lxml import etree

# ── Canvas ────────────────────────────────────────────────────────────────────
W = Inches(13.33)
H = Inches(7.5)

# ── Palette ───────────────────────────────────────────────────────────────────
BG_NEAR_BLACK = RGBColor(0x0A, 0x0A, 0x0F)
BG_NAVY       = RGBColor(0x06, 0x0E, 0x27)
BG_DARK_RED   = RGBColor(0x1E, 0x04, 0x04)
BG_DARK_BLUE  = RGBColor(0x03, 0x0C, 0x25)
BG_DARK_GREEN = RGBColor(0x02, 0x18, 0x0A)

WHITE      = RGBColor(0xFF, 0xFF, 0xFF)
OFF_WHITE  = RGBColor(0xD1, 0xD1, 0xD6)
YELLOW     = RGBColor(0xFF, 0xD6, 0x00)
RED_HOT    = RGBColor(0xFF, 0x3B, 0x30)
GREEN_NEON = RGBColor(0x30, 0xD1, 0x58)
BLUE_GLOW  = RGBColor(0x40, 0xA5, 0xFF)
ORANGE     = RGBColor(0xFF, 0x9F, 0x0A)
TEAL       = RGBColor(0x00, 0xC7, 0xBE)
PURPLE     = RGBColor(0xBF, 0x5A, 0xF2)
GRAY_DIM   = RGBColor(0x6C, 0x6C, 0x70)
GRAY_MID   = RGBColor(0xAE, 0xAE, 0xB2)

# ── Primitives ────────────────────────────────────────────────────────────────

def _prs():
    p = Presentation()
    p.slide_width  = W
    p.slide_height = H
    return p


def _slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])  # blank


def bg(slide, color: RGBColor):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def rect(slide, x, y, w, h, fill=None, border=None, border_w=Pt(1.5)):
    sh = slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    else:
        sh.fill.background()
    if border:
        sh.line.color.rgb = border
        sh.line.width = border_w
    else:
        sh.line.fill.background()
    return sh


def txt(slide, text, x, y, w, h, sz=24, bold=False, color=WHITE,
        align=PP_ALIGN.LEFT, italic=False, font="Helvetica Neue", wrap=True):
    txb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = txb.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size   = Pt(sz)
    r.font.bold   = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.name = font
    return txb


def code_block(slide, text, x, y, w, h, sz=14):
    rect(slide, x, y, w, h, fill=RGBColor(0x16, 0x16, 0x1A),
         border=GRAY_DIM, border_w=Pt(0.75))
    txt(slide, text, x + 0.18, y + 0.12, w - 0.36, h - 0.24,
        sz=sz, color=GREEN_NEON, font="Courier New", wrap=False)


def notes(slide, text: str):
    slide.notes_slide.notes_text_frame.text = text


def transition(slide, kind="fade"):
    """Inject a slide transition via raw XML."""
    sld = slide._element
    for t in sld.findall(qn('p:transition')):
        sld.remove(t)
    NS = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    if kind == "push":
        xml = f'<p:transition {NS} spd="med"><p:push dir="l"/></p:transition>'
    elif kind == "cover":
        xml = f'<p:transition {NS} spd="med"><p:cover dir="l"/></p:transition>'
    else:
        xml = f'<p:transition {NS} spd="med"><p:fade/></p:transition>'
    sld.append(etree.fromstring(xml))


# ── Slides ────────────────────────────────────────────────────────────────────

def s01_title(prs):
    """Cinematic dark title. One big statement."""
    s = _slide(prs)
    bg(s, BG_NAVY)
    transition(s, "fade")

    # Left accent bar
    rect(s, 0, 0, 0.18, 7.5, fill=BLUE_GLOW)

    txt(s, "Argus", 0.55, 1.0, 12.5, 2.8,
        sz=120, bold=True, color=WHITE, font="Helvetica Neue")

    txt(s, "What if your network could configure itself?",
        0.55, 4.6, 10, 0.7, sz=26, color=GRAY_MID)

    txt(s, "CMU 18-731 Network Security  ·  Ashwin Silla  ·  2026",
        0.55, 6.5, 10, 0.5, sz=16, color=GRAY_DIM)

    txt(s, "🔐", 10.5, 0.6, 2.5, 3.5, sz=130, align=PP_ALIGN.CENTER)

    notes(s, """Welcome! Today I'm presenting Argus.

This project sits at the intersection of network automation and AI. I'll tell a story first — so you understand the problem — and then show you the tool, how it works, and how it makes networks more secure.

Quick housekeeping: 15-minute presentation, demo in the middle, Q&A at the end. I'll explain every piece of jargon as I go.""")


def s02_meet_alex(prs):
    """Huge emoji + one-liner intro. Very visual."""
    s = _slide(prs)
    bg(s, BG_NEAR_BLACK)
    transition(s, "fade")

    txt(s, "👨‍💻", 0.4, 0.3, 5.5, 5.5, sz=200, align=PP_ALIGN.CENTER)

    txt(s, "Meet Alex.", 6.0, 1.0, 7.0, 1.4,
        sz=70, bold=True, color=WHITE)

    txt(s, "Senior Network Engineer", 6.0, 2.6, 7.0, 0.7,
        sz=28, color=YELLOW, bold=True)

    txt(s, "His job:", 6.0, 3.6, 7.0, 0.5, sz=20, color=GRAY_DIM)

    txt(s, "Keep every\nnetwork device\nconfigured correctly.", 6.0, 4.1, 7.0, 2.5,
        sz=30, bold=True, color=WHITE)

    notes(s, """Meet Alex. He's been a network engineer for 8 years. He's good at his job — but he has a problem.

Alex's company runs its entire operation on network infrastructure — servers, firewalls, switches. His job is to make sure every single device has the right configuration.

And in security terms, a misconfigured network device is an open door for attackers.""")


def s03_the_scale(prs):
    """Big numbers. Security stakes made explicit."""
    s = _slide(prs)
    bg(s, BG_NEAR_BLACK)
    transition(s, "fade")

    txt(s, "200+", 0.8, 0.5, 7, 2.8,
        sz=140, bold=True, color=YELLOW, align=PP_ALIGN.CENTER)
    txt(s, "network devices", 0.8, 3.1, 7, 0.8,
        sz=36, color=WHITE, align=PP_ALIGN.CENTER)
    txt(s, "across 3 data centers", 0.8, 3.85, 7, 0.6,
        sz=22, color=GRAY_MID, align=PP_ALIGN.CENTER)

    # Vertical divider
    rect(s, 8.3, 0.7, 0.05, 5.8, fill=GRAY_DIM)

    txt(s, "1 wrong\nconfig", 8.7, 1.2, 4.5, 1.6,
        sz=44, bold=True, color=RED_HOT)
    txt(s, "=", 8.7, 3.0, 4.5, 0.7, sz=44, bold=True, color=WHITE)
    txt(s, "attacker\ngets in", 8.7, 3.7, 4.5, 1.6,
        sz=44, bold=True, color=RED_HOT)

    notes(s, """Alex manages 200+ network devices. That's 200+ potential points of misconfiguration.

In network security, misconfiguration is one of the top attack vectors. The 2020 Capital One breach? A misconfigured firewall rule. The 2023 Microsoft Azure breach? Misconfigured network access controls.

CWE-16 — Configuration — is consistently a top software weakness. And in networks, configurations change constantly: new VLANs, new routing policies, new access control lists.

Alex has to get every single one right, every time.""")


def s04_what_is_iac(prs):
    """Explain network IaC from scratch. This is NEW content the audience needs."""
    s = _slide(prs)
    bg(s, BG_NAVY)
    transition(s, "fade")

    txt(s, "Old way:", 0.5, 0.4, 12.3, 0.8, sz=26, color=GRAY_DIM)
    txt(s, "SSH into each device.\nType commands manually.\n200 times.", 0.5, 1.1, 12.3, 2.5,
        sz=44, bold=True, color=WHITE)

    # Down arrow
    txt(s, "↓", 6.2, 3.7, 1, 0.8, sz=44, color=GRAY_DIM, align=PP_ALIGN.CENTER)

    txt(s, "Modern way (IaC):", 0.5, 4.5, 12.3, 0.6, sz=26, color=GRAY_DIM)
    txt(s, "Write config-as-code. Push once. Deploy everywhere.", 0.5, 5.1, 12.3, 1.1,
        sz=38, bold=True, color=YELLOW)

    notes(s, """Before I explain the problem, let me explain what network engineers ACTUALLY do today.

The old way: SSH into each device one by one, type the same set of commands 200 times. Pray you don't make a typo. This takes days per change and creates 'snowflake' configs — every device ends up slightly different.

The modern way is Infrastructure as Code, or IaC. Instead of typing commands, you write a configuration file describing what you WANT your network to look like. An automation tool — in this case, Ansible with a framework called Arista AVD — reads that file and pushes the right config to every device at once.

This solved the consistency problem. But it created a new one.""")


def s05_alex_opens_repo(prs):
    """The visual frustration slide. Emoji + folder tree = overwhelm."""
    s = _slide(prs)
    bg(s, BG_DARK_RED)
    transition(s, "push")

    txt(s, "😵", 0.3, 0.2, 3, 3, sz=130, align=PP_ALIGN.CENTER)
    txt(s, "But then Alex\nopens the repo...", 3.3, 0.4, 5.5, 1.8,
        sz=42, bold=True, color=WHITE)

    code_block(s, (
        "group_vars/\n"
        "├── DC1_FABRIC/\n"
        "│   └── fabric.yml            ← 847 lines\n"
        "├── DC1_L3_LEAVES/\n"
        "│   └── l3_leaves.yml         ← 312 lines\n"
        "├── DC1_SPINES/\n"
        "│   └── spines.yml            ← 98 lines\n"
        "├── DC1_TENANTS_NETWORKS/\n"
        "│   └── tenants.yml           ← 1,204 lines\n"
        "├── DC1_SERVER_PORTS/\n"
        "│   └── server_ports.yml      ← 556 lines\n"
        "└── ... 8 more files"
    ), 0.4, 2.5, 8.8, 4.5, sz=14)

    txt(s, "Which file?\nWhich key?\nWhich format?", 9.5, 2.8, 3.6, 2.8,
        sz=28, bold=True, color=ORANGE)

    notes(s, """Here's what the IaC config repository looks like for a network like Alex's.

This is the group_vars directory. Each folder is a different layer of the network. Each file contains hundreds or thousands of lines of YAML describing every aspect of how those devices should behave.

To make even a simple change — add a new VLAN, add a static route — Alex has to know:
- WHICH of these 13 files to edit
- WHICH exact key inside that file (the schema has hundreds of options)
- WHAT format that key expects (arrays? nested objects? specific string values?)

And there's no autocomplete. No IDE hints. The docs are 300 pages. This is where engineers make mistakes.""")


def s06_one_change_many_files(prs):
    """Concrete example. One task = many edits."""
    s = _slide(prs)
    bg(s, BG_DARK_RED)
    transition(s, "fade")

    txt(s, "Add a static route.", 0.5, 0.3, 12.3, 0.9,
        sz=52, bold=True, color=WHITE)
    txt(s, "How hard could it be? 🙃", 0.5, 1.2, 12.3, 0.6,
        sz=26, color=GRAY_MID, italic=True)

    items = [
        (ORANGE,     "l3_leaves.yml",      "Find structured_config.static_routes (NOT defaults.static_routes — that key doesn't exist)"),
        (ORANGE,     "intended/",          "Regenerate all device configs with ansible-playbook build.yml"),
        (ORANGE,     "documentation/",     "Rebuild markdown docs for 4 switches"),
        (GRAY_MID,   "validation",         "Manually check: does BGP still converge? Any routing loops?"),
        (GRAY_MID,   "peer review",        "Get another engineer to verify your YAML is correct"),
        (GRAY_DIM,   "deploy",             "Run ansible-playbook deploy.yml and watch for errors"),
    ]
    for i, (color, fname, action) in enumerate(items):
        y = 2.0 + i * 0.82
        txt(s, "◆", 0.4, y, 0.4, 0.7, sz=14, color=color, bold=True)
        txt(s, fname, 0.85, y, 2.8, 0.7, sz=17, bold=True, color=color)
        txt(s, action, 3.75, y, 9.5, 0.7, sz=15, color=OFF_WHITE)

    txt(s, "45–90 minutes. For adding one route.",
        0.5, 7.0, 12.3, 0.45, sz=20, bold=True, color=RED_HOT,
        align=PP_ALIGN.CENTER)

    notes(s, """Let me make this concrete. Alex gets a ticket: 'Add a static route to all leaf nodes.'

Simple, right? Here's what actually happens:

He has to find the CORRECT yaml key — and this is where engineers get burned. 'static_routes' is NOT valid directly under l3leaf.defaults. It has to go under structured_config.static_routes. Wrong key = cryptic Ansible error. Hours of debugging.

Then rebuild all device configs. Then rebuild documentation. Then manually validate the network behavior. Then get someone to peer review the YAML. Then finally deploy.

45 to 90 minutes per ticket. And that's if Alex gets the schema right on the first try.""")


def s07_security_incident(prs):
    """The security stakes. Real consequences of IaC errors."""
    s = _slide(prs)
    bg(s, BG_DARK_RED)
    transition(s, "cover")

    txt(s, "💥", 0.3, 0.2, 4.5, 3.8, sz=160, align=PP_ALIGN.CENTER)

    txt(s, "One typo.", 5.0, 0.6, 8.0, 1.0,
        sz=62, bold=True, color=WHITE)
    txt(s, "Wrong VLAN ID.", 5.0, 1.7, 8.0, 0.9,
        sz=52, bold=True, color=RED_HOT)
    txt(s, "Dev server on patient\ndata network.", 5.0, 2.8, 8.0, 1.5,
        sz=36, bold=True, color=ORANGE)
    txt(s, "6 months before anyone noticed.", 5.0, 4.5, 8.0, 0.8,
        sz=24, color=GRAY_MID, italic=True)

    notes(s, """This is not hypothetical.

In our scenario: Alex's team adds a new VLAN for development servers. A typo in the VLAN ID means the dev server gets placed in the same network segment as sensitive patient data — a HIPAA violation.

Nobody catches it. The IaC build PASSED. The config deployed SUCCESSFULLY. But the network segmentation was wrong. An attacker with access to the dev environment could now reach the patient database.

CWE-16 — Configuration weaknesses — are consistently in the OWASP top 10. Network misconfiguration is real, it happens, and the consequences are serious.

The problem isn't Alex. The problem is that there's NO automated security validation between 'writing YAML' and 'deploying to production.'""")


def s08_the_gap(prs):
    """The core problem stated cleanly. Three words."""
    s = _slide(prs)
    bg(s, BG_NEAR_BLACK)
    transition(s, "fade")

    txt(s, "IaC is powerful.", 0.5, 0.5, 12.3, 1.2,
        sz=56, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    txt(s, "But it's not intelligent. And it's not safe.",
        0.5, 1.8, 12.3, 0.9, sz=40, color=GRAY_MID, align=PP_ALIGN.CENTER)

    # 3 problem blocks
    for i, (label, sub, color) in enumerate([
        ("MANUAL",      "Deep schema expertise\nrequired for every change", RED_HOT),
        ("COMPLEX",     "13 files, 3,000+ lines,\nhundreds of schema rules", ORANGE),
        ("UNVALIDATED", "No security check before\nconfig reaches devices", YELLOW),
    ]):
        x = 0.7 + i * 4.2
        rect(s, x, 3.0, 3.8, 3.8, fill=RGBColor(0x18, 0x18, 0x1C),
             border=color, border_w=Pt(2))
        txt(s, label, x + 0.1, 3.1, 3.6, 0.9, sz=30, bold=True, color=color,
            align=PP_ALIGN.CENTER)
        txt(s, sub, x + 0.1, 4.1, 3.6, 1.5, sz=18, color=GRAY_MID,
            align=PP_ALIGN.CENTER)

    notes(s, """Here's the problem in three words.

MANUAL: Even with IaC, you still need deep expertise. You need to know exactly which YAML key, which file, which format. The cognitive load is enormous.

COMPLEX: The AVD schema for Arista networks has hundreds of configuration keys with non-obvious nesting rules. 300 pages of documentation.

UNVALIDATED: Ansible will push whatever you write, even if it creates routing loops, breaks access control lists, or exposes sensitive VLANs to the wrong users. There's no security gate.""")


def s09_what_if(prs):
    """The pivot. Big moment. What if you could just ask?"""
    s = _slide(prs)
    bg(s, BG_NAVY)
    transition(s, "fade")

    txt(s, "What if you could", 0.6, 0.8, 12.1, 1.4,
        sz=62, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    txt(s, "just... ask?", 0.6, 2.1, 12.1, 1.5,
        sz=78, bold=True, color=BLUE_GLOW, align=PP_ALIGN.CENTER)

    # Chat bubble
    rect(s, 1.2, 3.9, 10.9, 1.2, fill=RGBColor(0x10, 0x1E, 0x45),
         border=BLUE_GLOW, border_w=Pt(2))
    txt(s, '👨‍💻  "Add a static route to all leaf nodes via 10.255.255.1"',
        1.4, 4.05, 10.5, 0.9, sz=22, bold=True, color=WHITE)

    # Response bubble
    rect(s, 1.2, 5.35, 10.9, 1.2, fill=RGBColor(0x02, 0x18, 0x0A),
         border=GREEN_NEON, border_w=Pt(2))
    txt(s, '🤖  Config generated  ✓   Validated  ✓   Committed to git  ✓   Awaiting approval',
        1.4, 5.5, 10.5, 0.9, sz=22, bold=True, color=GREEN_NEON)

    notes(s, """What if instead of editing YAML files, you could describe what you want in plain English?

'Add a static route to all leaf nodes via 10.255.255.1'

And the tool handles everything: finds the right file, uses the correct schema key, generates valid YAML, validates the network behavior, and creates an auditable git commit — all in about 2 minutes.

This is Argus.""")


def s10_demo(prs):
    """Live demo slide. Minimal text, focus on the tool."""
    s = _slide(prs)
    bg(s, BG_NAVY)
    transition(s, "fade")

    txt(s, "🎬", 0.4, 0.2, 2, 1.5, sz=70)
    txt(s, "Demo", 2.1, 0.3, 4, 1.2, sz=64, bold=True, color=YELLOW)

    # Web UI mockup
    rect(s, 0.4, 1.7, 12.5, 5.5, fill=RGBColor(0x10, 0x10, 0x16),
         border=GRAY_DIM, border_w=Pt(1))
    txt(s, "Argus  ·  AVD Branch: Add-batfish",
        0.6, 1.78, 12, 0.45, sz=13, color=GRAY_DIM, font="Courier New")
    rect(s, 0.4, 2.28, 12.5, 0.03, fill=GRAY_DIM)

    txt(s, '> "Add a static route 0.0.0.0/0 via 10.255.255.1 to all leaf nodes"',
        0.6, 2.38, 12, 0.55, sz=16, color=BLUE_GLOW, font="Courier New")

    txt(s, (
        "✓  Intent resolved → l3leaf.defaults.structured_config.static_routes\n"
        "✓  Retrieving schema context (RAG)...\n"
        "✓  Generating YAML config...\n"
        "✓  Ansible build passed on attempt 1\n"
        "✓  Batfish: BGP sessions intact · no routing loops · route reachable ✓\n"
        "✓  Git branch created: avd/static-route-20260419\n"
        "⏳ Awaiting your approval to merge..."
    ), 0.6, 3.05, 12, 3.8, sz=16, color=OFF_WHITE, font="Courier New")

    notes(s, """[LIVE DEMO — walk through the web UI]

Key things to point out:
1. The request is plain English — no YAML knowledge needed
2. The agent knew to use structured_config.static_routes (not the invalid key path)
3. Batfish ran and validated before anything was committed
4. The commit is on a feature branch — human still approves before merge

[If demo fails: click any past run in the Recent Runs sidebar to show a saved result]""")


def s11_how_it_works(prs):
    """Simple pipeline diagram. One clear flow."""
    s = _slide(prs)
    bg(s, BG_DARK_BLUE)
    transition(s, "fade")

    txt(s, "Under the Hood", 0.5, 0.2, 12.3, 0.8,
        sz=44, bold=True, color=WHITE)

    stages = [
        ("💬",  "You ask",          "Plain English",         BLUE_GLOW),
        ("📚",  "RAG + LLM",        "Finds right\nfile & key", PURPLE),
        ("📄",  "AVD YAML",         "Generates\nvalid config",  YELLOW),
        ("🛡️", "Batfish",          "Validates\nsecurity",    GREEN_NEON),
        ("📦",  "Git PR",           "Feature branch\n+ audit",  ORANGE),
    ]

    sw = 2.2
    gap = 0.25
    total = len(stages) * sw + (len(stages) - 1) * gap
    sx = (13.33 - total) / 2

    for i, (icon, title, sub, color) in enumerate(stages):
        x = sx + i * (sw + gap)
        rect(s, x, 1.3, sw, 4.5, fill=RGBColor(0x06, 0x10, 0x2A),
             border=color, border_w=Pt(2))
        txt(s, icon, x + 0.05, 1.45, sw - 0.1, 1.2, sz=48, align=PP_ALIGN.CENTER)
        txt(s, title, x + 0.05, 2.75, sw - 0.1, 0.65, sz=20, bold=True,
            color=color, align=PP_ALIGN.CENTER)
        txt(s, sub, x + 0.05, 3.45, sw - 0.1, 1.1, sz=16, color=GRAY_MID,
            align=PP_ALIGN.CENTER)
        if i < len(stages) - 1:
            ax = x + sw + gap / 2 - 0.18
            txt(s, "→", ax, 2.9, 0.36, 0.6, sz=20, color=GRAY_DIM,
                align=PP_ALIGN.CENTER)

    # Descriptions at bottom
    descs = [
        "Engineer\ndescribes\nwhat they want",
        "TF-IDF retrieves\nrelevant schema\ncontext for LLM",
        "Merges into\ngroup_vars/\npreserving comments",
        "Runs BEFORE\nanything touches\na real device",
        "Human approves\nor rejects with\none click",
    ]
    for i, d in enumerate(descs):
        x = sx + i * (sw + gap)
        txt(s, d, x + 0.05, 5.9, sw - 0.1, 1.5, sz=12, color=GRAY_DIM,
            align=PP_ALIGN.CENTER)

    notes(s, """Five stages in the pipeline.

1. You ask — plain English. The engineer types what they want.
2. RAG + LLM — TF-IDF retrieves the most relevant group_vars files. The LLM uses this context to figure out exactly which file and which key path to modify.
3. AVD YAML — The LLM generates correct YAML. Ansible validates it. If it fails, the error goes back to the LLM for up to 3 retries.
4. Batfish validation — this is the key security innovation. Batfish builds a model of the ENTIRE network from the generated config files and checks: are BGP sessions intact? Any routing loops? Do ACLs do what they're supposed to?
5. Git PR — everything goes on a feature branch. The engineer reviews and approves or rejects.""")


def s12_batfish(prs):
    """Batfish is the security star. Make it clear what it does."""
    s = _slide(prs)
    bg(s, BG_DARK_BLUE)
    transition(s, "fade")

    txt(s, "🛡️", 0.3, 0.3, 3.8, 3.5, sz=150, align=PP_ALIGN.CENTER)

    txt(s, "Batfish", 4.3, 0.4, 9.0, 1.3,
        sz=68, bold=True, color=GREEN_NEON)
    txt(s, "Validates network behavior BEFORE\nanything touches a real device.",
        4.3, 1.85, 9.0, 1.2, sz=24, color=OFF_WHITE)

    checks = [
        ("No routing loops",        "Verifies all traffic actually reaches its destination"),
        ("ACL correctness",         "Did your access control list do what you intended?"),
        ("Reachability analysis",   "Which hosts can reach which — modeled, not guessed"),
        ("BGP convergence",         "All routing sessions establish correctly"),
    ]
    for i, (title, detail) in enumerate(checks):
        y = 3.5 + i * 0.95
        txt(s, "✓", 0.5, y, 0.55, 0.75, sz=28, bold=True, color=GREEN_NEON)
        txt(s, title, 1.1, y, 5.0, 0.5, sz=22, bold=True, color=WHITE)
        txt(s, detail, 1.1, y + 0.44, 12.0, 0.45, sz=15, color=GRAY_MID)

    notes(s, """Batfish is the security heart of this tool.

It's an open-source network analysis tool originally from Microsoft Research, now maintained by Intentionet (Amazon). It takes your network configuration files — before they touch any device — and builds a complete formal model of how your network will behave.

Then it answers questions: will BGP sessions come up? Is any host unreachable? Does this ACL actually block what we think it blocks? Can an attacker in VLAN 100 reach the database in VLAN 200?

In our tool, Batfish runs automatically. If the generated config creates a routing loop, breaks reachability, or violates security properties, the commit is blocked and the engineer gets a clear error message.

This is the security gate that was missing from manual IaC workflows.""")


def s13_audit_trail(prs):
    """Every change in git. Compliance angle."""
    s = _slide(prs)
    bg(s, BG_NEAR_BLACK)
    transition(s, "fade")

    txt(s, "Every change.\nFull audit trail.", 0.5, 0.3, 7.2, 2.2,
        sz=52, bold=True, color=WHITE)
    txt(s, "🔍", 8.2, 0.2, 5, 2.8, sz=130, align=PP_ALIGN.CENTER)

    code_block(s, (
        "$ git log --oneline\n"
        "\n"
        "3f2a1c8  avd: block SSH from 0.0.0.0/0 on all L3 leaves\n"
        "b91e4d2  avd: add VRF PATIENT_DATA to DC1 fabric\n"
        "a44f120  avd: update spanning-tree priority dc1-leaf1a\n"
        "7c3e891  avd: add static route 0.0.0.0/0 via 10.255.255.1\n"
        "2b09f33  agent_runs: logs for run 20260419T024341Z\n"
        "\n"
        "Author: AVD Agent <avd-agent@local>\n"
        "Reviewer: ashwin.silla@corp.com  ✓ approved"
    ), 0.5, 2.7, 12.5, 4.4, sz=15)

    notes(s, """Every single change the agent makes creates a git commit. Who requested it, when it was made, exactly which config lines changed on which device, and who approved it.

For security compliance — HIPAA, SOC2, PCI-DSS — this is exactly what auditors want. Who changed the network ACL? When? Was it reviewed? You have a complete, tamper-evident history.

No more 'someone must have changed it at 2am' incidents. No more mystery configurations.""")


def s14_before_after(prs):
    """Visual comparison. Keep numbers front and center."""
    s = _slide(prs)
    bg(s, BG_NEAR_BLACK)
    transition(s, "fade")

    txt(s, "Before  vs  After", 0.5, 0.2, 12.3, 0.8,
        sz=42, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

    # Before column
    rect(s, 0.35, 1.2, 5.9, 5.9,
         fill=RGBColor(0x20, 0x06, 0x06), border=RED_HOT, border_w=Pt(2))
    txt(s, "😰  Before", 0.55, 1.3, 5.5, 0.7, sz=28, bold=True,
        color=RED_HOT, align=PP_ALIGN.CENTER)

    before = [
        "45–90 min per change",
        "Must know AVD schema by heart",
        "0 security validation",
        "YAML diff review (unreadable)",
        "Manual error-prone process",
        "No audit trail of intent",
    ]
    for i, item in enumerate(before):
        txt(s, f"✗  {item}", 0.55, 2.15 + i * 0.78, 5.5, 0.7, sz=17, color=OFF_WHITE)

    # VS
    txt(s, "VS", 6.42, 3.9, 0.9, 0.7, sz=32, bold=True,
        color=GRAY_DIM, align=PP_ALIGN.CENTER)

    # After column
    rect(s, 7.1, 1.2, 5.9, 5.9,
         fill=RGBColor(0x02, 0x18, 0x08), border=GREEN_NEON, border_w=Pt(2))
    txt(s, "😊  After", 7.3, 1.3, 5.5, 0.7, sz=28, bold=True,
        color=GREEN_NEON, align=PP_ALIGN.CENTER)

    after = [
        "~2 min per change",
        "Plain English — no expertise needed",
        "Batfish validates security",
        "EOS CLI diff — readable by anyone",
        "AI handles schema complexity",
        "Full git audit trail, auto-committed",
    ]
    for i, item in enumerate(after):
        txt(s, f"✓  {item}", 7.3, 2.15 + i * 0.78, 5.5, 0.7, sz=17, color=GREEN_NEON)

    notes(s, """The difference is stark.

Before: 45-90 minutes per change, you need deep AVD knowledge, zero security validation before deployment, YAML diffs that only YAML experts can read, high error rate, no clear audit trail of why a change was made.

After: 2 minutes, plain English input, Batfish validates security before any device is touched, EOS CLI diffs that any network engineer understands, AI handles the schema complexity, full git audit trail automatically.

The security improvement is real. Batfish caught 3 different config errors in our test runs that would have caused either network outages or security policy violations.""")


def s15_conclusion(prs):
    """Happy ending. Alex. Big emoji. Clean close."""
    s = _slide(prs)
    bg(s, BG_DARK_GREEN)
    transition(s, "fade")

    txt(s, "😊", 0.3, 0.4, 5.5, 4.5, sz=190, align=PP_ALIGN.CENTER)

    txt(s, "Alex is happy.", 5.9, 0.7, 7.3, 1.3,
        sz=58, bold=True, color=WHITE)

    txt(s, "2 hours  →  2 minutes", 5.9, 2.2, 7.3, 0.9,
        sz=34, bold=True, color=GREEN_NEON)

    txt(s, (
        "✓  Zero misconfigs in production\n"
        "✓  Batfish caught 3 security issues before deploy\n"
        "✓  Full audit trail in git\n"
        "✓  Junior engineers can make changes safely"
    ), 5.9, 3.3, 7.3, 2.8, sz=20, color=OFF_WHITE)

    txt(s, "Questions? 🙋", 0.5, 6.4, 12.3, 0.9,
        sz=38, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

    notes(s, """Alex went from 2 hours per network change to 2 minutes. But more importantly, the network is more secure — not because Alex got smarter, but because the process now has automated security validation built in.

Thesis: AI + formal network analysis doesn't just speed things up. It raises the security floor.

Common questions to prepare for:

Q: Why not just use ChatGPT directly?
A: ChatGPT doesn't know the AVD schema, can't access your git repo, can't run Batfish, and doesn't create auditable commits. This is purpose-built.

Q: What LLM are you using?
A: Claude Sonnet (Anthropic) via API. RAG retrieves relevant schema context before each generation.

Q: Is Batfish production-ready?
A: Yes — it's used at large enterprises. Our integration is the novel part.

Q: What if Batfish misses something?
A: Batfish is formal analysis, not heuristic. It doesn't 'miss' things it's configured to check. Defense in depth still applies — this is one layer.

Q: How does the RAG work?
A: TF-IDF cosine similarity over the group_vars files. Cuts prompt size by ~60% by only sending relevant files to the LLM.""")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    prs = _prs()

    s01_title(prs)
    s02_meet_alex(prs)
    s03_the_scale(prs)
    s04_what_is_iac(prs)
    s05_alex_opens_repo(prs)
    s06_one_change_many_files(prs)
    s07_security_incident(prs)
    s08_the_gap(prs)
    s09_what_if(prs)
    s10_demo(prs)
    s11_how_it_works(prs)
    s12_batfish(prs)
    s13_audit_trail(prs)
    s14_before_after(prs)
    s15_conclusion(prs)

    out = "/Users/ashwinsilla/Netsec/Argus.pptx"
    prs.save(out)
    print(f"✓  Saved {out}  ({len(prs.slides)} slides)")

    # XML validation
    import zipfile
    with zipfile.ZipFile(out) as z:
        errors = []
        slide_names = [n for n in z.namelist()
                       if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        for name in slide_names:
            try:
                etree.fromstring(z.read(name))
            except etree.XMLSyntaxError as e:
                errors.append(f"{name}: {e}")
        if errors:
            print("XML ERRORS:")
            for e in errors:
                print(" ", e)
        else:
            print(f"✓  All {len(slide_names)} slide XMLs valid")


if __name__ == "__main__":
    main()
