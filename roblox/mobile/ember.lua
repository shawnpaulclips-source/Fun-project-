--[[ EMBER — mobile client suite
     Touch-first. GUI renderer (no Drawing lib), so it runs on Delta / Codex / Arceus / Hydrogen / Cacti.
     Drag the ember tab to move the panel. Tap it to collapse. ]]

local Players            = game:GetService("Players")
local RunService         = game:GetService("RunService")
local UserInputService   = game:GetService("UserInputService")
local TweenService       = game:GetService("TweenService")
local Lighting           = game:GetService("Lighting")
local Workspace          = game:GetService("Workspace")

local LP     = Players.LocalPlayer
local Camera = Workspace.CurrentCamera

----------------------------------------------------------------- config
local C = {
    esp          = true,
    box          = true,
    name         = true,
    health       = true,
    distance     = true,
    tracer       = false,
    skeleton     = false,
    chams        = false,
    teamCheck    = true,
    visibleOnly  = false,
    maxDistance  = 1200,
    espRate      = 1,            -- 1 = every frame, 2 = every other

    aim          = false,
    aimMode      = 1,            -- 1 Hold, 2 Toggle
    aimPart      = 1,            -- 1 Head, 2 Torso, 3 Nearest
    aimFov       = 140,
    aimSmooth    = 0.28,
    aimPredict   = 0.0,          -- musket balls are projectiles; crank this if they drop
    showFov      = true,
    aimVisible   = true,

    fullbright   = false,
    noFog        = false,
    fov          = 70,
    infJump      = false,
    speed        = 0,
}

local THEME = {
    bg      = Color3.fromRGB(13, 14, 17),
    panel   = Color3.fromRGB(20, 22, 27),
    raise   = Color3.fromRGB(28, 31, 38),
    stroke  = Color3.fromRGB(46, 50, 60),
    text    = Color3.fromRGB(236, 238, 243),
    dim     = Color3.fromRGB(133, 140, 152),
    accent  = Color3.fromRGB(255, 122, 61),
    accent2 = Color3.fromRGB(255, 181, 92),
    good    = Color3.fromRGB(96, 212, 140),
    bad     = Color3.fromRGB(238, 92, 92),
    enemy   = Color3.fromRGB(255, 108, 96),
    ally    = Color3.fromRGB(104, 196, 255),
}

local SAVE = "ember_mobile.json"

----------------------------------------------------------------- plumbing
local function new(class, props, parent)
    local i = Instance.new(class)
    for k, v in pairs(props or {}) do i[k] = v end
    if parent then i.Parent = parent end
    return i
end

local function corner(p, r)  return new("UICorner", {CornerRadius = UDim.new(0, r or 6)}, p) end
local function stroke(p, col, th, tr)
    return new("UIStroke", {
        Color = col or THEME.stroke, Thickness = th or 1, Transparency = tr or 0,
        ApplyStrokeMode = Enum.ApplyStrokeMode.Border,
    }, p)
end

local function host()
    local ok, g = pcall(function() return gethui() end)
    if ok and g then return g end
    ok, g = pcall(function() return game:GetService("CoreGui") end)
    if ok and g then return g end
    return LP:WaitForChild("PlayerGui")
end

local root = new("ScreenGui", {
    Name = "ember", ResetOnSpawn = false, IgnoreGuiInset = true,
    ZIndexBehavior = Enum.ZIndexBehavior.Sibling, DisplayOrder = 99,
})
pcall(function() if syn and syn.protect_gui then syn.protect_gui(root) end end)
pcall(function() if protectgui then protectgui(root) end end)
root.Parent = host()

local espLayer = new("Folder", {Name = "render"}, root)

local VP = Camera.ViewportSize
local PANEL_W = math.clamp(math.floor(VP.X * 0.44), 296, 392)

----------------------------------------------------------------- save / load
local canFile = (type(writefile) == "function" and type(readfile) == "function")
local function saveCfg()
    if not canFile then return end
    pcall(function() writefile(SAVE, game:GetService("HttpService"):JSONEncode(C)) end)
end
local function loadCfg()
    if not canFile or not isfile or not isfile(SAVE) then return end
    pcall(function()
        local t = game:GetService("HttpService"):JSONDecode(readfile(SAVE))
        for k, v in pairs(t) do if C[k] ~= nil then C[k] = v end end
    end)
end
loadCfg()

----------------------------------------------------------------- shell
local fab = new("TextButton", {
    Name = "fab", Text = "", AutoButtonColor = false,
    BackgroundColor3 = THEME.panel,
    Size = UDim2.fromOffset(56, 56),
    Position = UDim2.new(0, 16, 0, 96),
    ZIndex = 20,
}, root)
corner(fab, 16); stroke(fab, THEME.stroke)

local fabGlyph = new("Frame", {
    BackgroundColor3 = THEME.accent, BorderSizePixel = 0,
    AnchorPoint = Vector2.new(0.5, 0.5), Position = UDim2.fromScale(0.5, 0.5),
    Size = UDim2.fromOffset(18, 18), Rotation = 45,
}, fab)
corner(fabGlyph, 5)
new("UIGradient", {
    Rotation = 90,
    Color = ColorSequence.new(THEME.accent2, THEME.accent),
}, fabGlyph)

local win = new("Frame", {
    Name = "win", BackgroundColor3 = THEME.bg, BorderSizePixel = 0,
    Position = UDim2.new(0, 16, 0, 96),
    Size = UDim2.fromOffset(PANEL_W, 316),
    ClipsDescendants = true, Visible = false, ZIndex = 10,
}, root)
corner(win, 14); stroke(win, THEME.stroke)

local glow = new("Frame", {
    BackgroundColor3 = THEME.accent, BorderSizePixel = 0,
    Size = UDim2.new(1, 0, 0, 2),
}, win)
new("UIGradient", {
    Color = ColorSequence.new{
        ColorSequenceKeypoint.new(0, THEME.accent),
        ColorSequenceKeypoint.new(0.55, THEME.accent2),
        ColorSequenceKeypoint.new(1, THEME.accent),
    },
    Transparency = NumberSequence.new{
        NumberSequenceKeypoint.new(0, 0.15),
        NumberSequenceKeypoint.new(1, 0.65),
    },
}, glow)

local bar = new("Frame", {
    BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 46),
    Position = UDim2.new(0, 0, 0, 2),
}, win)

new("TextLabel", {
    BackgroundTransparency = 1, Text = "EMBER",
    Font = Enum.Font.GothamBlack, TextSize = 15, TextColor3 = THEME.text,
    TextXAlignment = Enum.TextXAlignment.Left,
    Position = UDim2.new(0, 16, 0, 8), Size = UDim2.new(1, -60, 0, 17),
}, bar)

local sub = new("TextLabel", {
    BackgroundTransparency = 1, Text = "idle",
    Font = Enum.Font.GothamMedium, TextSize = 10, TextColor3 = THEME.dim,
    TextXAlignment = Enum.TextXAlignment.Left,
    Position = UDim2.new(0, 16, 0, 25), Size = UDim2.new(1, -60, 0, 12),
}, bar)

local closeBtn = new("TextButton", {
    Text = "", AutoButtonColor = false, BackgroundColor3 = THEME.raise,
    Size = UDim2.fromOffset(26, 26), Position = UDim2.new(1, -38, 0, 10),
}, bar)
corner(closeBtn, 8)
new("Frame", {
    BackgroundColor3 = THEME.dim, BorderSizePixel = 0,
    AnchorPoint = Vector2.new(0.5, 0.5), Position = UDim2.fromScale(0.5, 0.5),
    Size = UDim2.fromOffset(11, 2),
}, closeBtn)

-- tabs
local tabRow = new("Frame", {
    BackgroundTransparency = 1, Position = UDim2.new(0, 12, 0, 48),
    Size = UDim2.new(1, -24, 0, 30),
}, win)
new("UIListLayout", {
    FillDirection = Enum.FillDirection.Horizontal, Padding = UDim.new(0, 4),
}, tabRow)

local underline = new("Frame", {
    BackgroundColor3 = THEME.accent, BorderSizePixel = 0,
    Position = UDim2.new(0, 12, 0, 77), Size = UDim2.fromOffset(40, 2), ZIndex = 3,
}, win)
corner(underline, 1)

local body = new("Frame", {
    BackgroundTransparency = 1, Position = UDim2.new(0, 0, 0, 84),
    Size = UDim2.new(1, 0, 1, -84),
}, win)

local pages, tabs = {}, {}
local active

local function page(label)
    local btn = new("TextButton", {
        Text = label:upper(), AutoButtonColor = false, BackgroundTransparency = 1,
        Font = Enum.Font.GothamBold, TextSize = 11, TextColor3 = THEME.dim,
        Size = UDim2.fromOffset(#label * 9 + 18, 30),
    }, tabRow)

    local scroll = new("ScrollingFrame", {
        BackgroundTransparency = 1, BorderSizePixel = 0, Visible = false,
        Size = UDim2.new(1, 0, 1, 0),
        CanvasSize = UDim2.new(), AutomaticCanvasSize = Enum.AutomaticSize.Y,
        ScrollBarThickness = 2, ScrollBarImageColor3 = THEME.stroke,
        ScrollingDirection = Enum.ScrollingDirection.Y, ClipsDescendants = true,
    }, body)
    new("UIPadding", {
        PaddingLeft = UDim.new(0, 12), PaddingRight = UDim.new(0, 12),
        PaddingTop = UDim.new(0, 4), PaddingBottom = UDim.new(0, 14),
    }, scroll)
    new("UIListLayout", {Padding = UDim.new(0, 6), SortOrder = Enum.SortOrder.LayoutOrder}, scroll)

    local function select()
        if active == scroll then return end
        for b, s in pairs(tabs) do
            s.Visible = false
            TweenService:Create(b, TweenInfo.new(0.18), {TextColor3 = THEME.dim}):Play()
        end
        scroll.Visible = true
        active = scroll
        TweenService:Create(btn, TweenInfo.new(0.18), {TextColor3 = THEME.text}):Play()
        TweenService:Create(underline, TweenInfo.new(0.3, Enum.EasingStyle.Quint), {
            Position = UDim2.new(0, 12 + btn.AbsolutePosition.X - tabRow.AbsolutePosition.X, 0, 77),
            Size = UDim2.fromOffset(btn.AbsoluteSize.X, 2),
        }):Play()
    end

    btn.MouseButton1Click:Connect(select)
    tabs[btn] = scroll
    pages[#pages + 1] = {btn = btn, scroll = scroll, select = select}
    return scroll
end

----------------------------------------------------------------- widgets
local function header(parent, txt)
    local l = new("TextLabel", {
        BackgroundTransparency = 1, Text = txt:upper(),
        Font = Enum.Font.GothamBold, TextSize = 9.5, TextColor3 = THEME.dim,
        TextXAlignment = Enum.TextXAlignment.Left,
        Size = UDim2.new(1, 0, 0, 22),
    }, parent)
    return l
end

local function toggle(parent, label, key, cb)
    local row = new("TextButton", {
        Text = "", AutoButtonColor = false, BackgroundColor3 = THEME.panel,
        Size = UDim2.new(1, 0, 0, 40),
    }, parent)
    corner(row, 9); stroke(row, THEME.stroke, 1, 0.4)

    new("TextLabel", {
        BackgroundTransparency = 1, Text = label,
        Font = Enum.Font.GothamMedium, TextSize = 12.5, TextColor3 = THEME.text,
        TextXAlignment = Enum.TextXAlignment.Left,
        Position = UDim2.new(0, 13, 0, 0), Size = UDim2.new(1, -70, 1, 0),
    }, row)

    local track = new("Frame", {
        BackgroundColor3 = C[key] and THEME.accent or THEME.raise, BorderSizePixel = 0,
        AnchorPoint = Vector2.new(1, 0.5), Position = UDim2.new(1, -13, 0.5, 0),
        Size = UDim2.fromOffset(38, 21),
    }, row)
    corner(track, 11)

    local knob = new("Frame", {
        BackgroundColor3 = C[key] and Color3.new(1, 1, 1) or THEME.dim, BorderSizePixel = 0,
        AnchorPoint = Vector2.new(0, 0.5),
        Position = C[key] and UDim2.new(0, 20, 0.5, 0) or UDim2.new(0, 3, 0.5, 0),
        Size = UDim2.fromOffset(15, 15),
    }, track)
    corner(knob, 8)

    local function paint(animate)
        local ti = TweenInfo.new(animate and 0.2 or 0, Enum.EasingStyle.Back, Enum.EasingDirection.Out, 0, false, 0)
        TweenService:Create(track, TweenInfo.new(0.2), {
            BackgroundColor3 = C[key] and THEME.accent or THEME.raise,
        }):Play()
        TweenService:Create(knob, ti, {
            Position = C[key] and UDim2.new(0, 20, 0.5, 0) or UDim2.new(0, 3, 0.5, 0),
            BackgroundColor3 = C[key] and Color3.new(1, 1, 1) or THEME.dim,
        }):Play()
    end

    row.MouseButton1Click:Connect(function()
        C[key] = not C[key]
        paint(true)
        if cb then cb(C[key]) end
        saveCfg()
    end)

    if cb then task.defer(cb, C[key]) end
    return row
end

local function slider(parent, label, key, min, max, step, suffix, cb)
    local row = new("Frame", {
        BackgroundColor3 = THEME.panel, BorderSizePixel = 0,
        Size = UDim2.new(1, 0, 0, 52),
    }, parent)
    corner(row, 9); stroke(row, THEME.stroke, 1, 0.4)

    new("TextLabel", {
        BackgroundTransparency = 1, Text = label,
        Font = Enum.Font.GothamMedium, TextSize = 12, TextColor3 = THEME.text,
        TextXAlignment = Enum.TextXAlignment.Left,
        Position = UDim2.new(0, 13, 0, 8), Size = UDim2.new(1, -80, 0, 14),
    }, row)

    local val = new("TextLabel", {
        BackgroundTransparency = 1, Text = "",
        Font = Enum.Font.GothamBold, TextSize = 11.5, TextColor3 = THEME.accent,
        TextXAlignment = Enum.TextXAlignment.Right,
        Position = UDim2.new(1, -13, 0, 8), Size = UDim2.fromOffset(0, 14),
        AnchorPoint = Vector2.new(1, 0), AutomaticSize = Enum.AutomaticSize.X,
    }, row)

    local track = new("Frame", {
        BackgroundColor3 = THEME.raise, BorderSizePixel = 0,
        Position = UDim2.new(0, 13, 0, 32), Size = UDim2.new(1, -26, 0, 6),
    }, row)
    corner(track, 3)

    local fill = new("Frame", {BackgroundColor3 = THEME.accent, BorderSizePixel = 0, Size = UDim2.new(0, 0, 1, 0)}, track)
    corner(fill, 3)
    new("UIGradient", {Color = ColorSequence.new(THEME.accent2, THEME.accent)}, fill)

    local grab = new("Frame", {
        BackgroundColor3 = Color3.new(1, 1, 1), BorderSizePixel = 0,
        AnchorPoint = Vector2.new(0.5, 0.5), Position = UDim2.new(0, 0, 0.5, 0),
        Size = UDim2.fromOffset(16, 16), ZIndex = 2,
    }, track)
    corner(grab, 8); stroke(grab, THEME.accent, 2)

    local hit = new("TextButton", {
        Text = "", BackgroundTransparency = 1, AutoButtonColor = false,
        Position = UDim2.new(0, 0, 0, 24), Size = UDim2.new(1, 0, 0, 28),
    }, row)

    local function render()
        local a = (C[key] - min) / (max - min)
        fill.Size = UDim2.new(a, 0, 1, 0)
        grab.Position = UDim2.new(a, 0, 0.5, 0)
        local shown = (step < 1) and string.format("%.2f", C[key]) or tostring(math.floor(C[key] + 0.5))
        val.Text = shown .. (suffix or "")
    end

    local function setFromX(x)
        local a = math.clamp((x - track.AbsolutePosition.X) / math.max(track.AbsoluteSize.X, 1), 0, 1)
        local raw = min + a * (max - min)
        C[key] = math.clamp(math.floor(raw / step + 0.5) * step, min, max)
        render()
        if cb then cb(C[key]) end
    end

    local dragging = false
    hit.InputBegan:Connect(function(io)
        if io.UserInputType == Enum.UserInputType.Touch or io.UserInputType == Enum.UserInputType.MouseButton1 then
            dragging = true
            setFromX(io.Position.X)
            TweenService:Create(grab, TweenInfo.new(0.12), {Size = UDim2.fromOffset(20, 20)}):Play()
        end
    end)
    hit.InputChanged:Connect(function(io)
        if dragging and (io.UserInputType == Enum.UserInputType.Touch or io.UserInputType == Enum.UserInputType.MouseMovement) then
            setFromX(io.Position.X)
        end
    end)
    local function stop(io)
        if dragging then
            dragging = false
            TweenService:Create(grab, TweenInfo.new(0.12), {Size = UDim2.fromOffset(16, 16)}):Play()
            saveCfg()
        end
    end
    hit.InputEnded:Connect(stop)
    UserInputService.InputEnded:Connect(stop)

    render()
    if cb then task.defer(cb, C[key]) end
    return row
end

local function cycler(parent, label, key, options, cb)
    local row = new("TextButton", {
        Text = "", AutoButtonColor = false, BackgroundColor3 = THEME.panel,
        Size = UDim2.new(1, 0, 0, 40),
    }, parent)
    corner(row, 9); stroke(row, THEME.stroke, 1, 0.4)

    new("TextLabel", {
        BackgroundTransparency = 1, Text = label,
        Font = Enum.Font.GothamMedium, TextSize = 12.5, TextColor3 = THEME.text,
        TextXAlignment = Enum.TextXAlignment.Left,
        Position = UDim2.new(0, 13, 0, 0), Size = UDim2.new(1, -120, 1, 0),
    }, row)

    local pill = new("Frame", {
        BackgroundColor3 = THEME.raise, BorderSizePixel = 0,
        AnchorPoint = Vector2.new(1, 0.5), Position = UDim2.new(1, -13, 0.5, 0),
        Size = UDim2.fromOffset(92, 25),
    }, row)
    corner(pill, 8)

    local txt = new("TextLabel", {
        BackgroundTransparency = 1, Text = options[C[key]] or options[1],
        Font = Enum.Font.GothamBold, TextSize = 11, TextColor3 = THEME.accent2,
        Size = UDim2.fromScale(1, 1),
    }, pill)

    row.MouseButton1Click:Connect(function()
        C[key] = (C[key] % #options) + 1
        txt.Text = options[C[key]]
        pill.BackgroundColor3 = THEME.accent
        txt.TextColor3 = Color3.new(1, 1, 1)
        TweenService:Create(pill, TweenInfo.new(0.25), {BackgroundColor3 = THEME.raise}):Play()
        TweenService:Create(txt, TweenInfo.new(0.25), {TextColor3 = THEME.accent2}):Play()
        if cb then cb(C[key]) end
        saveCfg()
    end)

    if cb then task.defer(cb, C[key]) end
    return row
end

local function action(parent, label, col, fn)
    local b = new("TextButton", {
        Text = label, AutoButtonColor = false, BackgroundColor3 = THEME.panel,
        Font = Enum.Font.GothamBold, TextSize = 12, TextColor3 = col,
        Size = UDim2.new(1, 0, 0, 38),
    }, parent)
    corner(b, 9); stroke(b, col, 1, 0.65)
    b.MouseButton1Click:Connect(function()
        TweenService:Create(b, TweenInfo.new(0.1), {BackgroundColor3 = THEME.raise}):Play()
        task.delay(0.12, function()
            TweenService:Create(b, TweenInfo.new(0.18), {BackgroundColor3 = THEME.panel}):Play()
        end)
        fn()
    end)
    return b
end

----------------------------------------------------------------- open / close
local open = false
local function setOpen(v)
    open = v
    if v then
        win.Visible = true
        win.Size = UDim2.fromOffset(PANEL_W, 60)
        win.BackgroundTransparency = 0.4
        TweenService:Create(win, TweenInfo.new(0.34, Enum.EasingStyle.Quint, Enum.EasingDirection.Out), {
            Size = UDim2.fromOffset(PANEL_W, 316), BackgroundTransparency = 0,
        }):Play()
        TweenService:Create(fab, TweenInfo.new(0.2), {BackgroundTransparency = 1}):Play()
        TweenService:Create(fabGlyph, TweenInfo.new(0.25), {Rotation = 135, BackgroundTransparency = 1}):Play()
        fab.Visible = false
        task.delay(0.05, function() if pages[1] then pages[1].select() end end)
    else
        local t = TweenService:Create(win, TweenInfo.new(0.22, Enum.EasingStyle.Quint, Enum.EasingDirection.In), {
            Size = UDim2.fromOffset(PANEL_W, 52), BackgroundTransparency = 1,
        })
        t.Completed:Connect(function() win.Visible = false end)
        t:Play()
        fab.Visible = true
        fab.Position = UDim2.fromOffset(win.AbsolutePosition.X, win.AbsolutePosition.Y)
        fab.BackgroundTransparency = 1
        fabGlyph.BackgroundTransparency = 1
        TweenService:Create(fab, TweenInfo.new(0.24), {BackgroundTransparency = 0}):Play()
        TweenService:Create(fabGlyph, TweenInfo.new(0.3, Enum.EasingStyle.Back), {Rotation = 45, BackgroundTransparency = 0}):Play()
    end
end

fab.MouseButton1Click:Connect(function() if not open then setOpen(true) end end)
closeBtn.MouseButton1Click:Connect(function() setOpen(false) end)

-- drag (title bar on the window, whole pad on the fab)
local function draggable(frame, handle)
    local dragging, startPos, startAbs = false, nil, nil
    handle.InputBegan:Connect(function(io)
        if io.UserInputType == Enum.UserInputType.Touch or io.UserInputType == Enum.UserInputType.MouseButton1 then
            dragging, startPos, startAbs = true, io.Position, frame.AbsolutePosition
        end
    end)
    handle.InputChanged:Connect(function(io)
        if dragging and (io.UserInputType == Enum.UserInputType.Touch or io.UserInputType == Enum.UserInputType.MouseMovement) then
            local d = io.Position - startPos
            frame.Position = UDim2.fromOffset(
                math.clamp(startAbs.X + d.X, 0, VP.X - frame.AbsoluteSize.X),
                math.clamp(startAbs.Y + d.Y, 0, VP.Y - 40)
            )
        end
    end)
    local function stop() dragging = false end
    handle.InputEnded:Connect(stop)
    UserInputService.InputEnded:Connect(stop)
end
draggable(win, bar)
draggable(fab, fab)

----------------------------------------------------------------- ESP engine
local pool = {}   -- [player] = {container, box, boxOut, hp, hpFill, nameL, infoL, tracer, bones={}, hl}

local R15 = {
    {"Head","UpperTorso"}, {"UpperTorso","LowerTorso"},
    {"UpperTorso","LeftUpperArm"}, {"LeftUpperArm","LeftLowerArm"}, {"LeftLowerArm","LeftHand"},
    {"UpperTorso","RightUpperArm"}, {"RightUpperArm","RightLowerArm"}, {"RightLowerArm","RightHand"},
    {"LowerTorso","LeftUpperLeg"}, {"LeftUpperLeg","LeftLowerLeg"}, {"LeftLowerLeg","LeftFoot"},
    {"LowerTorso","RightUpperLeg"}, {"RightUpperLeg","RightLowerLeg"}, {"RightLowerLeg","RightFoot"},
}
local R6 = {
    {"Head","Torso"}, {"Torso","Left Arm"}, {"Torso","Right Arm"},
    {"Torso","Left Leg"}, {"Torso","Right Leg"},
}
local MAX_BONES = #R15

local function build(plr)
    local c = new("Frame", {Name = plr.Name, BackgroundTransparency = 1, Size = UDim2.fromScale(1, 1), Visible = false}, espLayer)

    local boxOut = new("Frame", {BackgroundTransparency = 1, BorderSizePixel = 0, Visible = false}, c)
    corner(boxOut, 4); stroke(boxOut, Color3.new(0, 0, 0), 3, 0.35)
    local box = new("Frame", {BackgroundTransparency = 1, BorderSizePixel = 0, Size = UDim2.fromScale(1, 1)}, boxOut)
    corner(box, 4)
    local boxStroke = stroke(box, THEME.enemy, 1.4)

    local hp = new("Frame", {
        BackgroundColor3 = Color3.fromRGB(8, 8, 10), BorderSizePixel = 0, Visible = false,
        AnchorPoint = Vector2.new(1, 0),
    }, c)
    corner(hp, 2)
    local hpFill = new("Frame", {
        BackgroundColor3 = THEME.good, BorderSizePixel = 0,
        AnchorPoint = Vector2.new(0, 1), Position = UDim2.fromScale(0, 1), Size = UDim2.fromScale(1, 1),
    }, hp)
    corner(hpFill, 2)

    local nameL = new("TextLabel", {
        BackgroundTransparency = 1, Text = plr.DisplayName, Visible = false,
        Font = Enum.Font.GothamBold, TextSize = 12, TextColor3 = Color3.new(1, 1, 1),
        TextStrokeTransparency = 0.35, TextStrokeColor3 = Color3.new(0, 0, 0),
        AnchorPoint = Vector2.new(0.5, 1), AutomaticSize = Enum.AutomaticSize.X, Size = UDim2.fromOffset(0, 14),
    }, c)

    local infoL = new("TextLabel", {
        BackgroundTransparency = 1, Text = "", Visible = false,
        Font = Enum.Font.GothamMedium, TextSize = 10.5, TextColor3 = THEME.accent2,
        TextStrokeTransparency = 0.5, TextStrokeColor3 = Color3.new(0, 0, 0),
        AnchorPoint = Vector2.new(0.5, 0), AutomaticSize = Enum.AutomaticSize.X, Size = UDim2.fromOffset(0, 12),
    }, c)

    local tracer = new("Frame", {
        BackgroundColor3 = THEME.enemy, BorderSizePixel = 0, Visible = false,
        AnchorPoint = Vector2.new(0, 0.5), BackgroundTransparency = 0.25,
    }, c)

    local bones = {}
    for i = 1, MAX_BONES do
        bones[i] = new("Frame", {
            BackgroundColor3 = Color3.new(1, 1, 1), BorderSizePixel = 0, Visible = false,
            AnchorPoint = Vector2.new(0, 0.5), BackgroundTransparency = 0.15,
        }, c)
    end

    pool[plr] = {
        c = c, boxOut = boxOut, box = box, boxStroke = boxStroke,
        hp = hp, hpFill = hpFill, nameL = nameL, infoL = infoL,
        tracer = tracer, bones = bones, hl = nil,
    }
end

local function teardown(plr)
    local d = pool[plr]
    if not d then return end
    if d.hl then d.hl:Destroy() end
    d.c:Destroy()
    pool[plr] = nil
end

for _, p in ipairs(Players:GetPlayers()) do if p ~= LP then build(p) end end
Players.PlayerAdded:Connect(function(p) if p ~= LP then build(p) end end)
Players.PlayerRemoving:Connect(teardown)

local function alive(plr)
    local ch = plr.Character
    if not ch then return nil end
    local hum = ch:FindFirstChildOfClass("Humanoid")
    local hrp = ch:FindFirstChild("HumanoidRootPart") or ch.PrimaryPart
    if not hum or not hrp or hum.Health <= 0 then return nil end
    return ch, hum, hrp
end

local function hostile(plr)
    if not C.teamCheck then return true end
    if LP.Team and plr.Team then return plr.Team ~= LP.Team end
    return plr.TeamColor ~= LP.TeamColor
end

local function sees(targetChar, point)
    local params = RaycastParams.new()
    params.FilterType = Enum.RaycastFilterType.Exclude
    params.FilterDescendantsInstances = {LP.Character, targetChar, Camera}
    params.IgnoreWater = true
    local origin = Camera.CFrame.Position
    local dir = point - origin
    local hit = Workspace:Raycast(origin, dir, params)
    return hit == nil
end

local function setChams(plr, data, char, col)
    if C.chams and char then
        if not data.hl or data.hl.Parent ~= char then
            if data.hl then data.hl:Destroy() end
            data.hl = new("Highlight", {
                Adornee = char, FillTransparency = 0.62, OutlineTransparency = 0,
                DepthMode = Enum.HighlightDepthMode.AlwaysOnTop,
            }, char)
        end
        data.hl.FillColor = col
        data.hl.OutlineColor = col
        data.hl.Enabled = true
    elseif data.hl then
        data.hl.Enabled = false
    end
end

----------------------------------------------------------------- aim
local fovRing = new("Frame", {
    BackgroundTransparency = 1, Visible = false,
    AnchorPoint = Vector2.new(0.5, 0.5), Position = UDim2.fromScale(0.5, 0.5),
    ZIndex = 4,
}, root)
new("UICorner", {CornerRadius = UDim.new(1, 0)}, fovRing)
local fovStroke = stroke(fovRing, THEME.accent, 1.5, 0.45)

local lockBtn = new("TextButton", {
    Name = "lock", Text = "LOCK", AutoButtonColor = false,
    Font = Enum.Font.GothamBlack, TextSize = 12, TextColor3 = THEME.text,
    BackgroundColor3 = THEME.panel, BackgroundTransparency = 0.12,
    AnchorPoint = Vector2.new(1, 1), Position = UDim2.new(1, -22, 1, -110),
    Size = UDim2.fromOffset(74, 74), Visible = false, ZIndex = 20,
}, root)
corner(lockBtn, 37); stroke(lockBtn, THEME.accent, 2, 0.3)

local engaged, target = false, nil

local function partFor(char)
    if C.aimPart == 1 then
        return char:FindFirstChild("Head") or char:FindFirstChild("HumanoidRootPart")
    elseif C.aimPart == 2 then
        return char:FindFirstChild("UpperTorso") or char:FindFirstChild("Torso") or char:FindFirstChild("HumanoidRootPart")
    end
    local best, bd = nil, math.huge
    local center = VP / 2
    for _, p in ipairs(char:GetChildren()) do
        if p:IsA("BasePart") then
            local sp, on = Camera:WorldToViewportPoint(p.Position)
            if on then
                local d = (Vector2.new(sp.X, sp.Y) - center).Magnitude
                if d < bd then best, bd = p, d end
            end
        end
    end
    return best or char:FindFirstChild("HumanoidRootPart")
end

local function acquire()
    local center = VP / 2
    local best, bestD = nil, C.aimFov
    for _, plr in ipairs(Players:GetPlayers()) do
        if plr ~= LP and hostile(plr) then
            local char, _, hrp = alive(plr)
            if char then
                local aimAt = partFor(char)
                if aimAt then
                    local sp, on = Camera:WorldToViewportPoint(aimAt.Position)
                    if on then
                        local d = (Vector2.new(sp.X, sp.Y) - center).Magnitude
                        if d < bestD then
                            if (not C.aimVisible) or sees(char, aimAt.Position) then
                                best, bestD = plr, d
                            end
                        end
                    end
                end
            end
        end
    end
    return best
end

local function pressLock()
    if not C.aim then return end
    if C.aimMode == 1 then
        engaged = true
        target = acquire()
    else
        engaged = not engaged
        target = engaged and acquire() or nil
    end
    TweenService:Create(lockBtn, TweenInfo.new(0.15), {
        BackgroundColor3 = engaged and THEME.accent or THEME.panel,
        TextColor3 = engaged and Color3.new(1, 1, 1) or THEME.text,
    }):Play()
end

local function releaseLock()
    if C.aimMode == 1 then
        engaged, target = false, nil
        TweenService:Create(lockBtn, TweenInfo.new(0.2), {
            BackgroundColor3 = THEME.panel, TextColor3 = THEME.text,
        }):Play()
    end
end

lockBtn.InputBegan:Connect(function(io)
    if io.UserInputType == Enum.UserInputType.Touch or io.UserInputType == Enum.UserInputType.MouseButton1 then
        pressLock()
    end
end)
lockBtn.InputEnded:Connect(function(io)
    if io.UserInputType == Enum.UserInputType.Touch or io.UserInputType == Enum.UserInputType.MouseButton1 then
        releaseLock()
    end
end)

----------------------------------------------------------------- misc
local lightSave = {
    Brightness = Lighting.Brightness, ClockTime = Lighting.ClockTime,
    FogEnd = Lighting.FogEnd, FogStart = Lighting.FogStart,
    Ambient = Lighting.Ambient, OutdoorAmbient = Lighting.OutdoorAmbient,
    GlobalShadows = Lighting.GlobalShadows,
}

local function applyLight()
    if C.fullbright then
        Lighting.Brightness = 2.2
        Lighting.ClockTime = 13
        Lighting.GlobalShadows = false
        Lighting.Ambient = Color3.fromRGB(150, 150, 150)
        Lighting.OutdoorAmbient = Color3.fromRGB(150, 150, 150)
    else
        Lighting.Brightness = lightSave.Brightness
        Lighting.ClockTime = lightSave.ClockTime
        Lighting.GlobalShadows = lightSave.GlobalShadows
        Lighting.Ambient = lightSave.Ambient
        Lighting.OutdoorAmbient = lightSave.OutdoorAmbient
    end
    if C.noFog then
        Lighting.FogEnd = 1e6
        Lighting.FogStart = 1e6
    else
        Lighting.FogEnd = lightSave.FogEnd
        Lighting.FogStart = lightSave.FogStart
    end
end

UserInputService.JumpRequest:Connect(function()
    if C.infJump then
        local ch = LP.Character
        local hum = ch and ch:FindFirstChildOfClass("Humanoid")
        if hum then hum:ChangeState(Enum.HumanoidStateType.Jumping) end
    end
end)

----------------------------------------------------------------- pages
local pVis  = page("visuals")
local pAim  = page("aim")
local pMisc = page("misc")

header(pVis, "render")
toggle(pVis, "ESP master", "esp", function(v)
    if not v then
        for _, d in pairs(pool) do
            d.c.Visible = false
            if d.hl then d.hl.Enabled = false end
        end
    end
end)
toggle(pVis, "Boxes", "box")
toggle(pVis, "Names", "name")
toggle(pVis, "Health bars", "health")
toggle(pVis, "Distance", "distance")
toggle(pVis, "Tracers", "tracer")
toggle(pVis, "Skeleton", "skeleton")
toggle(pVis, "Chams", "chams", function(v)
    if not v then for _, d in pairs(pool) do if d.hl then d.hl.Enabled = false end end end
end)

header(pVis, "filters")
toggle(pVis, "Team check", "teamCheck")
toggle(pVis, "Visible only", "visibleOnly")
slider(pVis, "Max distance", "maxDistance", 100, 5000, 50, "m")
cycler(pVis, "Update rate", "espRate", {"Every frame", "Every 2nd", "Every 3rd"})

header(pAim, "camera lock")
toggle(pAim, "Enable aim", "aim", function(v) lockBtn.Visible = v end)
cycler(pAim, "Trigger", "aimMode", {"Hold", "Toggle"}, function()
    engaged, target = false, nil
    lockBtn.BackgroundColor3 = THEME.panel
    lockBtn.TextColor3 = THEME.text
end)
cycler(pAim, "Hit part", "aimPart", {"Head", "Torso", "Nearest"})
slider(pAim, "FOV radius", "aimFov", 20, 600, 5, "px", function(v)
    fovRing.Size = UDim2.fromOffset(v * 2, v * 2)
end)
slider(pAim, "Smoothing", "aimSmooth", 0.05, 1, 0.01, "")
slider(pAim, "Prediction", "aimPredict", 0, 0.5, 0.005, "s")
toggle(pAim, "Wall check", "aimVisible")
toggle(pAim, "Show FOV ring", "showFov", function(v) fovRing.Visible = v end)

header(pMisc, "world")
toggle(pMisc, "Fullbright", "fullbright", applyLight)
toggle(pMisc, "No fog", "noFog", applyLight)
slider(pMisc, "Camera FOV", "fov", 40, 120, 1, "", function(v) Camera.FieldOfView = v end)

header(pMisc, "movement")
toggle(pMisc, "Infinite jump", "infJump")
slider(pMisc, "Walk speed boost", "speed", 0, 80, 1, "", function(v)
    local ch = LP.Character
    local hum = ch and ch:FindFirstChildOfClass("Humanoid")
    if hum then hum.WalkSpeed = 16 + v end
end)

header(pMisc, "session")
action(pMisc, "RESET PANEL POSITION", THEME.dim, function()
    win.Position = UDim2.new(0, 16, 0, 96)
    fab.Position = UDim2.new(0, 16, 0, 96)
end)

local unload
action(pMisc, "UNLOAD EMBER", THEME.bad, function() unload() end)

----------------------------------------------------------------- loop
local frame = 0
local conn

conn = RunService.RenderStepped:Connect(function(dt)
    Camera = Workspace.CurrentCamera or Camera
    VP = Camera.ViewportSize
    frame = frame + 1

    -- aim
    if C.aim and engaged then
        if (not target) or (not alive(target)) or (C.teamCheck and not hostile(target)) then
            target = acquire()
        end
        if target then
            local char = target.Character
            if char then
                local aimAt = partFor(char)
                if aimAt then
                    local pos = aimAt.Position + aimAt.AssemblyLinearVelocity * C.aimPredict
                    local goal = CFrame.lookAt(Camera.CFrame.Position, pos)
                    local a = math.clamp(C.aimSmooth * (dt * 60), 0.02, 1)
                    Camera.CFrame = Camera.CFrame:Lerp(goal, a)
                end
            end
        end
    end

    fovRing.Visible = C.aim and C.showFov
    if fovRing.Visible then
        fovRing.Size = UDim2.fromOffset(C.aimFov * 2, C.aimFov * 2)
        fovStroke.Color = engaged and THEME.accent2 or THEME.accent
        fovStroke.Transparency = engaged and 0.15 or 0.5
    end

    -- esp
    if not C.esp then
        sub.Text = "idle"
        return
    end
    if frame % C.espRate ~= 0 then return end

    local shown = 0
    local camPos = Camera.CFrame.Position
    local botAnchor = Vector2.new(VP.X / 2, VP.Y)

    for plr, d in pairs(pool) do
        local char, hum, hrp = alive(plr)
        local vis = false

        if char then
            local enemy = hostile(plr)
            local dist = (hrp.Position - camPos).Magnitude

            local allow = dist <= C.maxDistance
            if C.teamCheck and not enemy then allow = false end

            if allow then
                local topS, onTop = Camera:WorldToViewportPoint(hrp.Position + Vector3.new(0, 3.2, 0))
                local botS      = Camera:WorldToViewportPoint(hrp.Position - Vector3.new(0, 3.4, 0))
                if onTop and topS.Z > 0 then
                    if C.visibleOnly and not sees(char, hrp.Position) then
                        allow = false
                    end

                    if allow then
                        vis = true
                        shown = shown + 1
                        local col = enemy and THEME.enemy or THEME.ally
                        local h = math.abs(botS.Y - topS.Y)
                        local w = h * 0.52
                        local x = topS.X - w / 2
                        local y = math.min(topS.Y, botS.Y)

                        d.boxStroke.Color = col
                        d.boxOut.Visible = C.box
                        if C.box then
                            d.boxOut.Position = UDim2.fromOffset(x, y)
                            d.boxOut.Size = UDim2.fromOffset(w, h)
                        end

                        d.hp.Visible = C.health
                        if C.health then
                            local f = math.clamp(hum.Health / math.max(hum.MaxHealth, 1), 0, 1)
                            d.hp.Position = UDim2.fromOffset(x - 5, y)
                            d.hp.Size = UDim2.fromOffset(3, h)
                            d.hpFill.Size = UDim2.fromScale(1, f)
                            d.hpFill.BackgroundColor3 = THEME.bad:Lerp(THEME.good, f)
                        end

                        d.nameL.Visible = C.name
                        if C.name then
                            d.nameL.Position = UDim2.fromOffset(x + w / 2, y - 3)
                            d.nameL.TextColor3 = col
                        end

                        d.infoL.Visible = C.distance
                        if C.distance then
                            d.infoL.Position = UDim2.fromOffset(x + w / 2, y + h + 2)
                            d.infoL.Text = string.format("%dm  %d hp", math.floor(dist), math.floor(hum.Health))
                        end

                        d.tracer.Visible = C.tracer
                        if C.tracer then
                            local p = Vector2.new(topS.X, math.max(topS.Y, botS.Y))
                            local delta = p - botAnchor
                            d.tracer.BackgroundColor3 = col
                            d.tracer.Position = UDim2.fromOffset(botAnchor.X, botAnchor.Y)
                            d.tracer.Size = UDim2.fromOffset(delta.Magnitude, 1.4)
                            d.tracer.Rotation = math.deg(math.atan2(delta.Y, delta.X))
                        end

                        if C.skeleton then
                            local set = char:FindFirstChild("UpperTorso") and R15 or R6
                            for i, pair in ipairs(set) do
                                local b = d.bones[i]
                                local a1 = char:FindFirstChild(pair[1])
                                local a2 = char:FindFirstChild(pair[2])
                                if a1 and a2 then
                                    local s1, o1 = Camera:WorldToViewportPoint(a1.Position)
                                    local s2, o2 = Camera:WorldToViewportPoint(a2.Position)
                                    if o1 and o2 then
                                        local v1 = Vector2.new(s1.X, s1.Y)
                                        local delta = Vector2.new(s2.X, s2.Y) - v1
                                        b.BackgroundColor3 = col
                                        b.Position = UDim2.fromOffset(v1.X, v1.Y)
                                        b.Size = UDim2.fromOffset(delta.Magnitude, 1.2)
                                        b.Rotation = math.deg(math.atan2(delta.Y, delta.X))
                                        b.Visible = true
                                    else
                                        b.Visible = false
                                    end
                                else
                                    b.Visible = false
                                end
                            end
                            for i = #set + 1, MAX_BONES do d.bones[i].Visible = false end
                        else
                            for i = 1, MAX_BONES do
                                if d.bones[i].Visible then d.bones[i].Visible = false end
                            end
                        end

                        setChams(plr, d, char, col)
                    end
                end
            end
        end

        if not vis then
            if d.c.Visible then
                d.c.Visible = false
                if d.hl then d.hl.Enabled = false end
            end
        else
            d.c.Visible = true
        end
    end

    sub.Text = shown .. " tracked  ·  " .. (engaged and target and ("locked " .. target.Name) or "no lock")
end)

LP.CharacterAdded:Connect(function(ch)
    local hum = ch:WaitForChild("Humanoid", 5)
    if hum and C.speed > 0 then hum.WalkSpeed = 16 + C.speed end
    task.defer(applyLight)
end)

applyLight()
Camera.FieldOfView = C.fov
lockBtn.Visible = C.aim
fovRing.Size = UDim2.fromOffset(C.aimFov * 2, C.aimFov * 2)

unload = function()
    if conn then conn:Disconnect() end
    for plr in pairs(pool) do teardown(plr) end
    C.fullbright, C.noFog = false, false
    applyLight()
    Camera.FieldOfView = 70
    root:Destroy()
end

setOpen(true)
