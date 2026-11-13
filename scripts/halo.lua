--[[
    HALO  ·  FOV wheel / silent aim / wallbang / ESP
    Toggle UI: RightShift      Panic unload: End
    Built against dump 109397169461300 (R6 rigs in workspace.Characters,
    Gun module raycasts with CollisionGroup "BulletsAndProjectiles").
]]

if getgenv().__HALO then pcall(getgenv().__HALO.Unload) end

local Players      = game:GetService("Players")
local RunService   = game:GetService("RunService")
local UIS          = game:GetService("UserInputService")
local TweenService = game:GetService("TweenService")
local RS           = game:GetService("ReplicatedStorage")

local LP     = Players.LocalPlayer
local Camera = workspace.CurrentCamera

local Settings = {
    Silent      = true,
    WallBang    = false,
    VisCheck    = true,
    TeamCheck   = true,
    TargetPart  = "Head",
    FOV         = 140,
    ShowFOV     = true,
    SpinFOV     = true,
    SnapLine    = true,
    Box         = true,
    Skeleton    = true,
    Health      = true,
    Names       = true,
    Distance    = true,
    UnhideEnemy = true,
    MaxDist     = 1500,
    Accent      = Color3.fromRGB(255, 92, 122),
    Enemy       = Color3.fromRGB(255, 92, 122),
    Locked      = Color3.fromRGB(120, 255, 190),
}

local Conns, Drawings = {}, {}
local function draw(kind, props)
    local d = Drawing.new(kind)
    for k, v in pairs(props) do d[k] = v end
    table.insert(Drawings, d)
    return d
end

------------------------------------------------------------------ game hooks
local CharFolder = workspace:FindFirstChild("Characters")
local Modules    = RS:FindFirstChild("Modules")
local MatchCtrl  = Modules and Modules:FindFirstChild("Controllers") and Modules.Controllers:FindFirstChild("MatchController")
local FriendlyFn = MatchCtrl and MatchCtrl:FindFirstChild("IsPlayerFriendly")

local function getChar(plr)
    local c = plr.Character
    if c and c.Parent then return c end
    CharFolder = CharFolder or workspace:FindFirstChild("Characters")
    return CharFolder and CharFolder:FindFirstChild(plr.Name)
end

local friendlyCache = {}
local function isFriendly(plr)
    if not Settings.TeamCheck then return false end
    local t = os.clock()
    local c = friendlyCache[plr]
    if c and t - c[2] < 0.5 then return c[1] end
    local ok, res = false, nil
    if FriendlyFn then ok, res = pcall(FriendlyFn.Invoke, FriendlyFn, plr) end
    if not ok or type(res) ~= "boolean" then
        res = (plr.Team ~= nil and plr.Team == LP.Team)
    end
    friendlyCache[plr] = { res, t }
    return res
end

local function isAlive(plr, char)
    local hum = char and char:FindFirstChildOfClass("Humanoid")
    if not hum or hum.Health <= 0 then return false end
    if char:GetAttribute("Dead") then return false end
    return true
end

-- the client-side EnemyHiderController reparents enemies you "can't see";
-- neuter it so ESP and wallbang have bodies to work with
local function unhide()
    if not Settings.UnhideEnemy then return end
    local mod = Modules and Modules.Controllers:FindFirstChild("EnemyHiderController")
    if not mod then return end
    local ok, ctrl = pcall(require, mod)
    if not ok or type(ctrl) ~= "table" then return end
    ctrl.ShouldHide  = function() return false end
    ctrl._HidePlayer = function() end
    ctrl.IsHidden    = function() return false end
    if ctrl._ShowAll then pcall(ctrl._ShowAll, ctrl) end
end
pcall(unhide)

------------------------------------------------------------------ targeting
local rayParams = RaycastParams.new()
rayParams.FilterType = Enum.RaycastFilterType.Exclude

local function visible(part, char)
    local myChar = getChar(LP)
    rayParams.FilterDescendantsInstances = { myChar, Camera, char }
    local origin = Camera.CFrame.Position
    return workspace:Raycast(origin, part.Position - origin, rayParams) == nil
end

local Target -- { plr, char, part, screen }
local function acquire()
    local center = Camera.ViewportSize / 2
    local best, bestD = nil, Settings.FOV
    local camPos = Camera.CFrame.Position
    for _, plr in ipairs(Players:GetPlayers()) do
        if plr ~= LP and not isFriendly(plr) then
            local char = getChar(plr)
            if char and isAlive(plr, char) then
                local part = char:FindFirstChild(Settings.TargetPart) or char:FindFirstChild("Head")
                if part and (part.Position - camPos).Magnitude <= Settings.MaxDist then
                    local sp, on = Camera:WorldToViewportPoint(part.Position)
                    if on then
                        local d = (Vector2.new(sp.X, sp.Y) - center).Magnitude
                        if d < bestD and (Settings.WallBang or not Settings.VisCheck or visible(part, char)) then
                            best, bestD = { plr = plr, char = char, part = part, screen = Vector2.new(sp.X, sp.Y) }, d
                        end
                    end
                end
            end
        end
    end
    return best
end

------------------------------------------------------------------ silent aim + wallbang
-- the Gun module traces bullets with workspace:Raycast using params whose
-- CollisionGroup is "BulletsAndProjectiles"; that result becomes the client
-- hit prediction sent to the server. Rewrite direction (and, for wallbang,
-- the filter) only for those calls.
local oldNamecall
oldNamecall = hookmetamethod(game, "__namecall", newcclosure(function(self, ...)
    if not checkcaller() and self == workspace and getnamecallmethod() == "Raycast" and Settings.Silent then
        local origin, dir, params = ...
        local t = Target
        if t and t.part and t.part.Parent and typeof(origin) == "Vector3" and typeof(dir) == "Vector3"
            and typeof(params) == "RaycastParams" and params.CollisionGroup == "BulletsAndProjectiles" then
            local aim = t.part.Position - origin
            local len = math.max(dir.Magnitude, aim.Magnitude + 2)
            local newDir = aim.Unit * len
            if Settings.WallBang then
                local p = RaycastParams.new()
                p.FilterType = Enum.RaycastFilterType.Include
                p.FilterDescendantsInstances = { t.char }
                p.CollisionGroup = params.CollisionGroup
                local res = oldNamecall(self, origin, newDir, p)
                if res then return res end
            end
            return oldNamecall(self, origin, newDir, params)
        end
    end
    return oldNamecall(self, ...)
end))

------------------------------------------------------------------ FOV wheel
local fovRing  = draw("Circle", { Thickness = 1.5, NumSides = 96, Filled = false, Transparency = 0.9, Color = Settings.Accent })
local fovGlow  = draw("Circle", { Thickness = 6,   NumSides = 96, Filled = false, Transparency = 0.15, Color = Settings.Accent })
local dot      = draw("Circle", { Radius = 3, Filled = true, NumSides = 20, Color = Color3.new(1, 1, 1), Transparency = 1 })
local dotRing  = draw("Circle", { Radius = 6, Filled = false, Thickness = 1, NumSides = 20, Color = Settings.Accent, Transparency = 0.8 })
local snap     = draw("Line",   { Thickness = 1.6, Color = Settings.Locked, Transparency = 1 })
local snapHead = draw("Circle", { Radius = 5, Filled = false, Thickness = 1.6, NumSides = 24, Color = Settings.Locked })
local ticks = {}
for i = 1, 8 do ticks[i] = draw("Line", { Thickness = 2, Color = Settings.Accent, Transparency = 0.9 }) end

local spin, pulse, lockLerp = 0, 0, 0
local function updateWheel(dt)
    local c = Camera.ViewportSize / 2
    local show = Settings.ShowFOV
    pulse = pulse + dt * 2.2
    if Settings.SpinFOV then spin = spin + dt * (Target and 2.4 or 0.7) end
    lockLerp = lockLerp + ((Target and 1 or 0) - lockLerp) * math.min(1, dt * 10)

    local col = Settings.Accent:Lerp(Settings.Locked, lockLerp)
    local r = Settings.FOV * (1 - 0.04 * lockLerp) + math.sin(pulse) * 1.5

    fovRing.Visible, fovGlow.Visible = show, show
    fovRing.Position, fovGlow.Position = c, c
    fovRing.Radius, fovGlow.Radius = r, r
    fovRing.Color, fovGlow.Color = col, col
    fovGlow.Transparency = 0.1 + 0.08 * (math.sin(pulse * 1.5) + 1)

    for i, l in ipairs(ticks) do
        local a = spin + (i - 1) * (math.pi / 4)
        local dirv = Vector2.new(math.cos(a), math.sin(a))
        local len = (i % 2 == 0) and 10 or 18
        l.From, l.To = c + dirv * (r - len * 0.5), c + dirv * (r + len * 0.5)
        l.Color, l.Visible = col, show
    end

    dot.Position, dotRing.Position = c, c
    dotRing.Radius = 6 + math.sin(pulse * 2) * 1.2
    dotRing.Color = col
    dot.Visible, dotRing.Visible = show, show

    if Target and Settings.SnapLine then
        local sp = Camera:WorldToViewportPoint(Target.part.Position)
        local hp = Vector2.new(sp.X, sp.Y)
        snap.From, snap.To, snap.Visible = c, hp, true
        snapHead.Position, snapHead.Visible = hp, true
        local wb = Settings.WallBang and not visible(Target.part, Target.char)
        local lc = wb and Color3.fromRGB(255, 200, 80) or Settings.Locked
        snap.Color, snapHead.Color = lc, lc
    else
        snap.Visible, snapHead.Visible = false, false
    end
end

------------------------------------------------------------------ ESP
local R6Bones = {
    { "Head", "Torso" }, { "Torso", "Left Arm" }, { "Torso", "Right Arm" },
    { "Torso", "Left Leg" }, { "Torso", "Right Leg" },
}
local ESP = {}

local function newESP()
    local e = {
        boxO  = draw("Square", { Thickness = 3, Filled = false, Color = Color3.new(0, 0, 0), Transparency = 0.6 }),
        box   = draw("Square", { Thickness = 1, Filled = false }),
        hpBg  = draw("Square", { Filled = true, Color = Color3.new(0, 0, 0), Transparency = 0.6 }),
        hp    = draw("Square", { Filled = true }),
        name  = draw("Text",   { Size = 13, Center = true, Outline = true, Font = 2, Color = Color3.new(1, 1, 1) }),
        dist  = draw("Text",   { Size = 12, Center = true, Outline = true, Font = 2, Color = Color3.fromRGB(200, 200, 210) }),
        bones = {},
    }
    for i = 1, #R6Bones do e.bones[i] = draw("Line", { Thickness = 1.2 }) end
    return e
end

local function hideESP(e)
    e.boxO.Visible, e.box.Visible, e.hpBg.Visible, e.hp.Visible = false, false, false, false
    e.name.Visible, e.dist.Visible = false, false
    for _, l in ipairs(e.bones) do l.Visible = false end
end

local function w2s(p)
    local v, on = Camera:WorldToViewportPoint(p)
    return Vector2.new(v.X, v.Y), on, v.Z
end

local function updateESP()
    local camPos = Camera.CFrame.Position
    for _, plr in ipairs(Players:GetPlayers()) do
        if plr ~= LP then
            local e = ESP[plr] or newESP()
            ESP[plr] = e
            local char = getChar(plr)
            local root = char and char:FindFirstChild("HumanoidRootPart")
            local hum = char and char:FindFirstChildOfClass("Humanoid")
            if not (root and hum and isAlive(plr, char)) or isFriendly(plr)
                or (root.Position - camPos).Magnitude > Settings.MaxDist then
                hideESP(e)
            else
                local cf, size = char:GetBoundingBox()
                local hs = size / 2
                local minX, minY, maxX, maxY = math.huge, math.huge, -math.huge, -math.huge
                local anyOn = false
                for x = -1, 1, 2 do for y = -1, 1, 2 do for z = -1, 1, 2 do
                    local p, on, depth = w2s((cf * CFrame.new(hs.X * x, hs.Y * y, hs.Z * z)).Position)
                    if depth > 0 then
                        anyOn = anyOn or on
                        minX, minY = math.min(minX, p.X), math.min(minY, p.Y)
                        maxX, maxY = math.max(maxX, p.X), math.max(maxY, p.Y)
                    end
                end end end

                if not anyOn then
                    hideESP(e)
                else
                    local locked = Target and Target.plr == plr
                    local col = locked and Settings.Locked or Settings.Enemy
                    local pos, sz = Vector2.new(minX, minY), Vector2.new(maxX - minX, maxY - minY)

                    e.box.Position, e.box.Size, e.box.Color, e.box.Visible = pos, sz, col, Settings.Box
                    e.boxO.Position, e.boxO.Size, e.boxO.Visible = pos, sz, Settings.Box

                    local frac = math.clamp(hum.Health / math.max(hum.MaxHealth, 1), 0, 1)
                    e.hpBg.Position, e.hpBg.Size = Vector2.new(minX - 6, minY - 1), Vector2.new(4, sz.Y + 2)
                    e.hp.Position = Vector2.new(minX - 5, minY + sz.Y * (1 - frac))
                    e.hp.Size = Vector2.new(2, sz.Y * frac)
                    e.hp.Color = Color3.fromRGB(255, 70, 90):Lerp(Color3.fromRGB(110, 255, 160), frac)
                    e.hpBg.Visible, e.hp.Visible = Settings.Health, Settings.Health

                    e.name.Text = plr.DisplayName
                    e.name.Position, e.name.Visible = Vector2.new((minX + maxX) / 2, minY - 16), Settings.Names
                    e.dist.Text = string.format("%dm", (root.Position - camPos).Magnitude // 3.57)
                    e.dist.Position, e.dist.Visible = Vector2.new((minX + maxX) / 2, maxY + 2), Settings.Distance

                    for i, b in ipairs(R6Bones) do
                        local l = e.bones[i]
                        local a, c2 = char:FindFirstChild(b[1]), char:FindFirstChild(b[2])
                        if Settings.Skeleton and a and c2 then
                            local pa, oa = w2s(a.Position)
                            local pb, ob = w2s(c2.Position)
                            l.From, l.To, l.Color, l.Visible = pa, pb, col, oa or ob
                        else
                            l.Visible = false
                        end
                    end
                end
            end
        end
    end
end

table.insert(Conns, Players.PlayerRemoving:Connect(function(plr)
    local e = ESP[plr]
    if e then
        for _, d in pairs(e) do
            if type(d) == "table" then for _, l in ipairs(d) do pcall(l.Remove, l) end else pcall(d.Remove, d) end
        end
        ESP[plr] = nil
    end
    friendlyCache[plr] = nil
end))

------------------------------------------------------------------ UI
local parentGui = (gethui and gethui()) or game:GetService("CoreGui")
local Gui = Instance.new("ScreenGui")
Gui.Name, Gui.ResetOnSpawn, Gui.ZIndexBehavior = "Halo", false, Enum.ZIndexBehavior.Sibling
Gui.IgnoreGuiInset = true
Gui.Parent = parentGui

local C = {
    bg     = Color3.fromRGB(14, 13, 18),
    panel  = Color3.fromRGB(21, 20, 27),
    raised = Color3.fromRGB(30, 28, 38),
    line   = Color3.fromRGB(44, 41, 55),
    text   = Color3.fromRGB(236, 233, 242),
    sub    = Color3.fromRGB(140, 134, 156),
}
local function tw(o, t, p, style)
    TweenService:Create(o, TweenInfo.new(t, style or Enum.EasingStyle.Quint, Enum.EasingDirection.Out), p):Play()
end
local function make(cls, props, children)
    local o = Instance.new(cls)
    for k, v in pairs(props) do o[k] = v end
    for _, ch in ipairs(children or {}) do ch.Parent = o end
    return o
end
local function corner(r) return make("UICorner", { CornerRadius = UDim.new(0, r) }) end
local function stroke(c, t) return make("UIStroke", { Color = c, Thickness = t or 1, ApplyStrokeMode = Enum.ApplyStrokeMode.Border }) end

local Main = make("Frame", {
    Size = UDim2.fromOffset(520, 360), Position = UDim2.new(0.5, -260, 0.5, -180),
    BackgroundColor3 = C.bg, BorderSizePixel = 0, Parent = Gui, ClipsDescendants = true,
}, { corner(12), stroke(C.line) })
local Scale = make("UIScale", { Scale = 0.9, Parent = Main })

make("Frame", {
    Size = UDim2.new(1, 0, 0, 2), BackgroundColor3 = Settings.Accent, BorderSizePixel = 0, Parent = Main,
}, { make("UIGradient", {
    Color = ColorSequence.new({
        ColorSequenceKeypoint.new(0, Settings.Accent),
        ColorSequenceKeypoint.new(0.5, Color3.fromRGB(180, 120, 255)),
        ColorSequenceKeypoint.new(1, Settings.Locked),
    }),
}) })

local Side = make("Frame", {
    Size = UDim2.new(0, 140, 1, -2), Position = UDim2.fromOffset(0, 2),
    BackgroundColor3 = C.panel, BorderSizePixel = 0, Parent = Main,
})
make("TextLabel", {
    Text = "HALO", Font = Enum.Font.GothamBlack, TextSize = 22, TextColor3 = C.text,
    BackgroundTransparency = 1, Position = UDim2.fromOffset(18, 16), Size = UDim2.fromOffset(110, 24),
    TextXAlignment = Enum.TextXAlignment.Left, Parent = Side,
})
make("TextLabel", {
    Text = "precision suite", Font = Enum.Font.Gotham, TextSize = 11, TextColor3 = C.sub,
    BackgroundTransparency = 1, Position = UDim2.fromOffset(18, 40), Size = UDim2.fromOffset(110, 14),
    TextXAlignment = Enum.TextXAlignment.Left, Parent = Side,
})
local TabHolder = make("Frame", {
    BackgroundTransparency = 1, Position = UDim2.fromOffset(10, 74), Size = UDim2.new(1, -20, 1, -110), Parent = Side,
}, { make("UIListLayout", { Padding = UDim.new(0, 4) }) })
local Indicator = make("Frame", {
    Size = UDim2.fromOffset(3, 18), Position = UDim2.fromOffset(0, 83), BackgroundColor3 = Settings.Accent,
    BorderSizePixel = 0, Parent = Side,
}, { corner(2) })
local Status = make("TextLabel", {
    Text = "● idle", Font = Enum.Font.GothamMedium, TextSize = 11, TextColor3 = C.sub,
    BackgroundTransparency = 1, Position = UDim2.new(0, 18, 1, -28), Size = UDim2.fromOffset(120, 14),
    TextXAlignment = Enum.TextXAlignment.Left, Parent = Side,
})

local Pages = make("Frame", {
    BackgroundTransparency = 1, Position = UDim2.fromOffset(152, 14), Size = UDim2.new(1, -164, 1, -26), Parent = Main,
})

-- drag
do
    local dragging, start, startPos
    Side.InputBegan:Connect(function(i)
        if i.UserInputType == Enum.UserInputType.MouseButton1 then
            dragging, start, startPos = true, i.Position, Main.Position
        end
    end)
    table.insert(Conns, UIS.InputChanged:Connect(function(i)
        if dragging and i.UserInputType == Enum.UserInputType.MouseMovement then
            local d = i.Position - start
            tw(Main, 0.12, { Position = UDim2.new(startPos.X.Scale, startPos.X.Offset + d.X, startPos.Y.Scale, startPos.Y.Offset + d.Y) })
        end
    end))
    table.insert(Conns, UIS.InputEnded:Connect(function(i)
        if i.UserInputType == Enum.UserInputType.MouseButton1 then dragging = false end
    end))
end

local tabs, currentPage = {}, nil
local function Tab(name)
    local btn = make("TextButton", {
        Text = "   " .. name, Font = Enum.Font.GothamMedium, TextSize = 13, TextColor3 = C.sub,
        BackgroundColor3 = C.raised, BackgroundTransparency = 1, AutoButtonColor = false,
        Size = UDim2.new(1, 0, 0, 30), TextXAlignment = Enum.TextXAlignment.Left, Parent = TabHolder,
    }, { corner(7) })
    local page = make("ScrollingFrame", {
        BackgroundTransparency = 1, Size = UDim2.fromScale(1, 1), CanvasSize = UDim2.new(),
        AutomaticCanvasSize = Enum.AutomaticSize.Y, ScrollBarThickness = 2, ScrollBarImageColor3 = Settings.Accent,
        BorderSizePixel = 0, Visible = false, Parent = Pages,
    }, { make("UIListLayout", { Padding = UDim.new(0, 6) }), make("UIPadding", { PaddingRight = UDim.new(0, 6) }) })

    local function select()
        for _, t in ipairs(tabs) do
            tw(t.btn, 0.25, { BackgroundTransparency = 1, TextColor3 = C.sub })
            t.page.Visible = false
        end
        tw(btn, 0.25, { BackgroundTransparency = 0, TextColor3 = C.text })
        tw(Indicator, 0.35, { Position = UDim2.fromOffset(0, 74 + btn.AbsolutePosition.Y - TabHolder.AbsolutePosition.Y + 6) })
        page.Visible = true
        page.Position = UDim2.fromOffset(0, 10)
        tw(page, 0.3, { Position = UDim2.fromOffset(0, 0) })
        currentPage = page
    end
    btn.MouseEnter:Connect(function() if currentPage ~= page then tw(btn, 0.15, { TextColor3 = C.text }) end end)
    btn.MouseLeave:Connect(function() if currentPage ~= page then tw(btn, 0.15, { TextColor3 = C.sub }) end end)
    btn.MouseButton1Click:Connect(select)
    table.insert(tabs, { btn = btn, page = page })
    return page, select
end

local function Section(page, title)
    make("TextLabel", {
        Text = string.upper(title), Font = Enum.Font.GothamBold, TextSize = 10, TextColor3 = C.sub,
        BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 20), TextXAlignment = Enum.TextXAlignment.Left,
        TextYAlignment = Enum.TextYAlignment.Bottom, Parent = page,
    })
end

local function Row(page, h)
    local r = make("Frame", {
        BackgroundColor3 = C.panel, Size = UDim2.new(1, 0, 0, h or 38), BorderSizePixel = 0, Parent = page,
    }, { corner(8) })
    local s = stroke(C.line); s.Parent = r
    r.MouseEnter:Connect(function() tw(s, 0.2, { Color = Settings.Accent:Lerp(C.line, 0.55) }) end)
    r.MouseLeave:Connect(function() tw(s, 0.2, { Color = C.line }) end)
    return r
end

local function Toggle(page, label, key, desc, onChange)
    local r = Row(page, desc and 46 or 38)
    make("TextLabel", {
        Text = label, Font = Enum.Font.GothamMedium, TextSize = 13, TextColor3 = C.text, BackgroundTransparency = 1,
        Position = UDim2.fromOffset(12, desc and 7 or 0), Size = UDim2.new(1, -70, 0, desc and 18 or 38),
        TextXAlignment = Enum.TextXAlignment.Left, Parent = r,
    })
    if desc then
        make("TextLabel", {
            Text = desc, Font = Enum.Font.Gotham, TextSize = 11, TextColor3 = C.sub, BackgroundTransparency = 1,
            Position = UDim2.fromOffset(12, 25), Size = UDim2.new(1, -70, 0, 14),
            TextXAlignment = Enum.TextXAlignment.Left, Parent = r,
        })
    end
    local track = make("Frame", {
        Size = UDim2.fromOffset(36, 20), Position = UDim2.new(1, -48, 0.5, -10), BackgroundColor3 = C.raised,
        BorderSizePixel = 0, Parent = r,
    }, { corner(10) })
    local knob = make("Frame", {
        Size = UDim2.fromOffset(14, 14), Position = UDim2.fromOffset(3, 3), BackgroundColor3 = C.sub,
        BorderSizePixel = 0, Parent = track,
    }, { corner(7) })
    local function render(anim)
        local on = Settings[key]
        local t = anim and 0.25 or 0
        tw(track, t, { BackgroundColor3 = on and Settings.Accent or C.raised })
        tw(knob, t, { Position = on and UDim2.fromOffset(19, 3) or UDim2.fromOffset(3, 3), BackgroundColor3 = on and Color3.new(1, 1, 1) or C.sub }, Enum.EasingStyle.Back)
    end
    render(false)
    local hit = make("TextButton", { Text = "", BackgroundTransparency = 1, Size = UDim2.fromScale(1, 1), Parent = r })
    hit.MouseButton1Down:Connect(function() tw(knob, 0.1, { Size = UDim2.fromOffset(18, 14) }) end)
    hit.MouseButton1Up:Connect(function() tw(knob, 0.15, { Size = UDim2.fromOffset(14, 14) }) end)
    hit.MouseButton1Click:Connect(function()
        Settings[key] = not Settings[key]
        render(true)
        if onChange then onChange(Settings[key]) end
    end)
end

local function Slider(page, label, key, min, max, suffix)
    local r = Row(page, 52)
    make("TextLabel", {
        Text = label, Font = Enum.Font.GothamMedium, TextSize = 13, TextColor3 = C.text, BackgroundTransparency = 1,
        Position = UDim2.fromOffset(12, 6), Size = UDim2.new(1, -80, 0, 20), TextXAlignment = Enum.TextXAlignment.Left, Parent = r,
    })
    local val = make("TextLabel", {
        Text = "", Font = Enum.Font.GothamBold, TextSize = 12, TextColor3 = Settings.Accent, BackgroundTransparency = 1,
        Position = UDim2.new(1, -72, 0, 6), Size = UDim2.fromOffset(60, 20), TextXAlignment = Enum.TextXAlignment.Right, Parent = r,
    })
    local bar = make("Frame", {
        Size = UDim2.new(1, -24, 0, 4), Position = UDim2.new(0, 12, 1, -14), BackgroundColor3 = C.raised,
        BorderSizePixel = 0, Parent = r,
    }, { corner(2) })
    local fill = make("Frame", { BackgroundColor3 = Settings.Accent, BorderSizePixel = 0, Size = UDim2.fromScale(0, 1), Parent = bar }, { corner(2) })
    local knob = make("Frame", {
        Size = UDim2.fromOffset(12, 12), AnchorPoint = Vector2.new(0.5, 0.5), Position = UDim2.fromScale(0, 0.5),
        BackgroundColor3 = Color3.new(1, 1, 1), BorderSizePixel = 0, Parent = bar,
    }, { corner(6) })
    local function set(v, anim)
        v = math.clamp(math.floor(v + 0.5), min, max)
        Settings[key] = v
        local a = (v - min) / (max - min)
        tw(fill, anim and 0.08 or 0, { Size = UDim2.fromScale(a, 1) })
        tw(knob, anim and 0.08 or 0, { Position = UDim2.fromScale(a, 0.5) })
        val.Text = tostring(v) .. (suffix or "")
    end
    set(Settings[key], false)
    local sliding = false
    local hit = make("TextButton", { Text = "", BackgroundTransparency = 1, Size = UDim2.new(1, 0, 0, 22), Position = UDim2.new(0, 0, 1, -24), Parent = r })
    local function upd(x) set(min + (max - min) * math.clamp((x - bar.AbsolutePosition.X) / bar.AbsoluteSize.X, 0, 1), true) end
    hit.MouseButton1Down:Connect(function(x) sliding = true; upd(x); tw(knob, 0.15, { Size = UDim2.fromOffset(16, 16) }) end)
    table.insert(Conns, UIS.InputChanged:Connect(function(i)
        if sliding and i.UserInputType == Enum.UserInputType.MouseMovement then upd(i.Position.X) end
    end))
    table.insert(Conns, UIS.InputEnded:Connect(function(i)
        if sliding and i.UserInputType == Enum.UserInputType.MouseButton1 then
            sliding = false; tw(knob, 0.15, { Size = UDim2.fromOffset(12, 12) })
        end
    end))
end

local function Choice(page, label, key, options)
    local r = Row(page, 38)
    make("TextLabel", {
        Text = label, Font = Enum.Font.GothamMedium, TextSize = 13, TextColor3 = C.text, BackgroundTransparency = 1,
        Position = UDim2.fromOffset(12, 0), Size = UDim2.new(0.5, 0, 1, 0), TextXAlignment = Enum.TextXAlignment.Left, Parent = r,
    })
    local holder = make("Frame", {
        BackgroundColor3 = C.raised, Size = UDim2.fromOffset(#options * 62, 24), Position = UDim2.new(1, -(#options * 62) - 8, 0.5, -12),
        BorderSizePixel = 0, Parent = r,
    }, { corner(6) })
    local pill = make("Frame", { BackgroundColor3 = Settings.Accent, Size = UDim2.fromOffset(60, 20), BorderSizePixel = 0, Parent = holder }, { corner(5) })
    local btns = {}
    local function render(anim)
        for i, o in ipairs(options) do
            local on = Settings[key] == o
            if on then tw(pill, anim and 0.3 or 0, { Position = UDim2.fromOffset(2 + (i - 1) * 62, 2) }, Enum.EasingStyle.Quint) end
            tw(btns[i], 0.2, { TextColor3 = on and Color3.new(1, 1, 1) or C.sub })
        end
    end
    for i, o in ipairs(options) do
        btns[i] = make("TextButton", {
            Text = o, Font = Enum.Font.GothamMedium, TextSize = 11, BackgroundTransparency = 1, TextColor3 = C.sub,
            Size = UDim2.fromOffset(62, 24), Position = UDim2.fromOffset((i - 1) * 62, 0), ZIndex = 2, Parent = holder,
        })
        btns[i].MouseButton1Click:Connect(function() Settings[key] = o; render(true) end)
    end
    render(false)
end

local combat, selectCombat = Tab("Combat")
Section(combat, "Silent aim")
Toggle(combat, "Silent aim", "Silent", "Bullets follow the snap line")
Toggle(combat, "Visibility check", "VisCheck", "Ignore targets behind cover")
Toggle(combat, "Team check", "TeamCheck", "Uses the game's IsPlayerFriendly")
Choice(combat, "Target", "TargetPart", { "Head", "Torso" })
Section(combat, "Experimental")
Toggle(combat, "Wallbang", "WallBang", "Trace ignores the map; server may reject")
Slider(combat, "Max distance", "MaxDist", 100, 3000, " st")

local visuals = Tab("Visuals")
Section(visuals, "FOV wheel")
Toggle(visuals, "Show wheel", "ShowFOV")
Toggle(visuals, "Spin", "SpinFOV")
Toggle(visuals, "Snap line", "SnapLine")
Slider(visuals, "Radius", "FOV", 30, 500, " px")
Section(visuals, "Players")
Toggle(visuals, "Box", "Box")
Toggle(visuals, "Skeleton", "Skeleton")
Toggle(visuals, "Health bar", "Health")
Toggle(visuals, "Names", "Names")
Toggle(visuals, "Distance", "Distance")

local misc = Tab("Misc")
Section(misc, "Game")
Toggle(misc, "Unhide enemies", "UnhideEnemy", "Disables EnemyHiderController", function(on) if on then pcall(unhide) end end)
Section(misc, "Keys")
local keysRow = Row(misc, 52)
make("TextLabel", {
    Text = "RightShift  ·  toggle menu\nEnd  ·  unload everything", Font = Enum.Font.Gotham, TextSize = 12,
    TextColor3 = C.sub, BackgroundTransparency = 1, Size = UDim2.new(1, -24, 1, 0), Position = UDim2.fromOffset(12, 0),
    TextXAlignment = Enum.TextXAlignment.Left, Parent = keysRow,
})

task.defer(selectCombat)

local open = true
local function setOpen(v)
    open = v
    if v then
        Main.Visible = true
        tw(Scale, 0.35, { Scale = 1 }, Enum.EasingStyle.Back)
        tw(Main, 0.25, { BackgroundTransparency = 0 })
    else
        tw(Scale, 0.2, { Scale = 0.9 })
        tw(Main, 0.2, { BackgroundTransparency = 1 })
        task.delay(0.2, function() if not open then Main.Visible = false end end)
    end
end
setOpen(true)

------------------------------------------------------------------ loop / lifecycle
local unloaded = false
local function Unload()
    if unloaded then return end
    unloaded = true
    Settings.Silent = false
    for _, c in ipairs(Conns) do pcall(c.Disconnect, c) end
    for _, d in ipairs(Drawings) do pcall(d.Remove, d) end
    pcall(Gui.Destroy, Gui)
    getgenv().__HALO = nil
end
getgenv().__HALO = { Unload = Unload, Settings = Settings }

table.insert(Conns, UIS.InputBegan:Connect(function(i, gp)
    if i.KeyCode == Enum.KeyCode.RightShift then setOpen(not open)
    elseif i.KeyCode == Enum.KeyCode.End then Unload() end
end))

local hiderTick = 0
table.insert(Conns, RunService.RenderStepped:Connect(function(dt)
    Camera = workspace.CurrentCamera
    Target = acquire()
    updateWheel(dt)
    updateESP()

    hiderTick = hiderTick + dt
    if hiderTick > 3 then hiderTick = 0; pcall(unhide) end

    if Target then
        Status.Text = "● " .. Target.plr.DisplayName
        Status.TextColor3 = Settings.WallBang and Color3.fromRGB(255, 200, 80) or Settings.Locked
    else
        Status.Text = "● idle"
        Status.TextColor3 = C.sub
    end
end))
