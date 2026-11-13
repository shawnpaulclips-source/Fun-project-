--[[
	skeleton + box esp  —  place 118245546740416
	players + bots (Dummy-template rigs tagged with BotUsername) + training dummies

	F1  box          F2  skeleton       F3  visibility check
	F4  box style    F5  bots on/off    END unload
]]

if getgenv().__MARA_ESP then pcall(getgenv().__MARA_ESP) end

local Players          = game:GetService("Players")
local RunService       = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")
local CollectionService = game:GetService("CollectionService")
local Workspace        = game:GetService("Workspace")

local LocalPlayer = Players.LocalPlayer
local Camera = Workspace.CurrentCamera

local CONFIG = {
	Box           = true,
	BoxStyle      = "Corner",   -- "Corner" | "Full"
	BoxFill       = true,
	Skeleton      = true,
	HeadCircle    = true,
	HealthBar     = true,
	Names         = true,
	Distance      = true,
	VisCheck      = true,
	ShowBots      = true,
	ShowDummies   = false,
	TeamCheck     = true,
	MaxDistance   = 1200,
	Smoothing     = 22,       -- box follow speed, higher = snappier
	FadeSpeed     = 9,
	Font          = 2,

	PlayerColor   = Color3.fromRGB(120, 200, 255),
	BotColor      = Color3.fromRGB(255, 176, 72),
	DummyColor    = Color3.fromRGB(170, 170, 185),
	HiddenTint    = 0.45,     -- color multiplier when occluded
	OutlineColor  = Color3.fromRGB(8, 8, 12),
	HealthHigh    = Color3.fromRGB(90, 255, 140),
	HealthMid     = Color3.fromRGB(255, 220, 70),
	HealthLow     = Color3.fromRGB(255, 60, 70),
	GhostColor    = Color3.fromRGB(255, 255, 255),
}

local HITBOX_NAMES = { HeadHitbox = true, BodyHitbox = true, LegsHitbox = true }

local R15_BONES = {
	{ "Head", "UpperTorso" },
	{ "UpperTorso", "LowerTorso" },
	{ "UpperTorso", "LeftUpperArm" },  { "LeftUpperArm", "LeftLowerArm" },   { "LeftLowerArm", "LeftHand" },
	{ "UpperTorso", "RightUpperArm" }, { "RightUpperArm", "RightLowerArm" }, { "RightLowerArm", "RightHand" },
	{ "LowerTorso", "LeftUpperLeg" },  { "LeftUpperLeg", "LeftLowerLeg" },   { "LeftLowerLeg", "LeftFoot" },
	{ "LowerTorso", "RightUpperLeg" }, { "RightUpperLeg", "RightLowerLeg" }, { "RightLowerLeg", "RightFoot" },
}

-- R6 limbs are single blocks, so bones run between virtual joint offsets in part space
local R6_BONES = {
	{ "Head", Vector3.new(0, 0, 0),      "Torso", Vector3.new(0, 1, 0) },
	{ "Torso", Vector3.new(0, 1, 0),     "Torso", Vector3.new(0, -1, 0) },
	{ "Torso", Vector3.new(0, 1, 0),     "Left Arm", Vector3.new(0, 0.8, 0) },
	{ "Left Arm", Vector3.new(0, 0.8, 0), "Left Arm", Vector3.new(0, -0.9, 0) },
	{ "Torso", Vector3.new(0, 1, 0),     "Right Arm", Vector3.new(0, 0.8, 0) },
	{ "Right Arm", Vector3.new(0, 0.8, 0), "Right Arm", Vector3.new(0, -0.9, 0) },
	{ "Torso", Vector3.new(0, -1, 0),    "Left Leg", Vector3.new(0, 0.8, 0) },
	{ "Left Leg", Vector3.new(0, 0.8, 0), "Left Leg", Vector3.new(0, -0.9, 0) },
	{ "Torso", Vector3.new(0, -1, 0),    "Right Leg", Vector3.new(0, 0.8, 0) },
	{ "Right Leg", Vector3.new(0, 0.8, 0), "Right Leg", Vector3.new(0, -0.9, 0) },
}

local BOUND_PARTS_R15 = { "Head", "UpperTorso", "LowerTorso", "LeftHand", "RightHand", "LeftFoot", "RightFoot", "LeftUpperArm", "RightUpperArm" }
local BOUND_PARTS_R6  = { "Head", "Torso", "Left Arm", "Right Arm", "Left Leg", "Right Leg" }

local CORNERS = {
	Vector3.new( 1,  1,  1), Vector3.new( 1,  1, -1), Vector3.new( 1, -1,  1), Vector3.new( 1, -1, -1),
	Vector3.new(-1,  1,  1), Vector3.new(-1,  1, -1), Vector3.new(-1, -1,  1), Vector3.new(-1, -1, -1),
}

------------------------------------------------------------------------ drawing helpers

local function new(class, props)
	local d = Drawing.new(class)
	for k, v in pairs(props) do d[k] = v end
	return d
end

local function line(thick, zi)
	return new("Line", { Thickness = thick, Visible = false, ZIndex = zi or 2 })
end

local function text(size)
	return new("Text", {
		Size = size, Center = true, Outline = true, OutlineColor = CONFIG.OutlineColor,
		Font = CONFIG.Font, Visible = false, ZIndex = 4,
	})
end

local function scale(c, m)
	return Color3.new(c.R * m, c.G * m, c.B * m)
end

local function healthColor(p)
	if p > 0.5 then return CONFIG.HealthMid:Lerp(CONFIG.HealthHigh, (p - 0.5) * 2) end
	return CONFIG.HealthLow:Lerp(CONFIG.HealthMid, p * 2)
end

local function approach(cur, target, speed, dt)
	return cur + (target - cur) * (1 - math.exp(-speed * dt))
end

------------------------------------------------------------------------ entity classification

local function classify(model)
	local plr = Players:GetPlayerFromCharacter(model)
	if plr then
		if plr == LocalPlayer then return nil end
		return "Player", plr
	end
	if model:GetAttribute("BotUsername") ~= nil or model:GetAttribute("BotAvatarUserId") ~= nil then
		return "Bot"
	end
	if CollectionService:HasTag(model, "Dummy") or model.Name == "Dummy" then
		return "Dummy"
	end
	return "Bot" -- any other non-player humanoid rig in a combat place is server AI
end

local function displayName(e)
	if e.kind == "Player" then
		local p = e.player
		return p.DisplayName ~= p.Name and (p.DisplayName .. " (@" .. p.Name .. ")") or p.Name
	end
	local n = e.model:GetAttribute("BotUsername")
	return (type(n) == "string" and n ~= "") and n or e.model.Name
end

------------------------------------------------------------------------ entity lifecycle

local Entities = {}
local Connections = {}

local function makeVisuals()
	local v = {}
	v.boxGlow    = new("Square", { Thickness = 4, Filled = false, Visible = false, ZIndex = 1 })
	v.boxFill    = new("Square", { Thickness = 1, Filled = true,  Visible = false, ZIndex = 1 })
	v.boxOutline = new("Square", { Thickness = 3, Filled = false, Visible = false, ZIndex = 2, Color = CONFIG.OutlineColor })
	v.box        = new("Square", { Thickness = 1, Filled = false, Visible = false, ZIndex = 3 })

	v.corners, v.cornerOutlines = {}, {}
	for i = 1, 8 do
		v.cornerOutlines[i] = line(3, 2)
		v.cornerOutlines[i].Color = CONFIG.OutlineColor
		v.corners[i] = line(1, 3)
	end

	v.hpBack  = new("Square", { Filled = true, Visible = false, ZIndex = 2, Color = CONFIG.OutlineColor })
	v.hpGhost = new("Square", { Filled = true, Visible = false, ZIndex = 3, Color = CONFIG.GhostColor })
	v.hpBar   = new("Square", { Filled = true, Visible = false, ZIndex = 4 })
	v.hpText  = text(11)

	v.name = text(13)
	v.tag  = text(11)
	v.dist = text(11)

	v.bones, v.boneOutlines = {}, {}
	for i = 1, 14 do
		v.boneOutlines[i] = line(3, 2)
		v.boneOutlines[i].Color = CONFIG.OutlineColor
		v.bones[i] = line(1.5, 3)
	end
	v.headOutline = new("Circle", { Thickness = 3, NumSides = 24, Filled = false, Visible = false, ZIndex = 2, Color = CONFIG.OutlineColor })
	v.head        = new("Circle", { Thickness = 1.5, NumSides = 24, Filled = false, Visible = false, ZIndex = 3 })
	return v
end

local function eachDrawing(v, fn)
	for _, d in pairs(v) do
		if type(d) == "table" and rawget(d, 1) ~= nil then
			for _, x in ipairs(d) do fn(x) end
		else
			fn(d)
		end
	end
end

local function hideAll(v)
	eachDrawing(v, function(d) d.Visible = false end)
end

local function destroyEntity(model)
	local e = Entities[model]
	if not e then return end
	Entities[model] = nil
	for _, c in ipairs(e.conns) do c:Disconnect() end
	eachDrawing(e.v, function(d) pcall(d.Remove, d) end)
end

local function track(model)
	if Entities[model] or model == LocalPlayer.Character then return end
	local hum = model:FindFirstChildOfClass("Humanoid")
	if not hum then return end
	local kind, plr = classify(model)
	if not kind then return end

	local e = {
		model = model, hum = hum, kind = kind, player = plr,
		v = makeVisuals(), conns = {},
		alpha = 0, box = nil, hp = 1, ghost = 1, ghostHold = 0,
		dying = false, spawnT = os.clock(), visible = true, visT = 0,
	}
	e.name = displayName(e)

	table.insert(e.conns, model.AncestryChanged:Connect(function(_, parent)
		if not parent then e.dying = true end
	end))
	table.insert(e.conns, model:GetAttributeChangedSignal("BotUsername"):Connect(function()
		e.kind = classify(model) or e.kind
		e.name = displayName(e)
	end))
	Entities[model] = e
end

------------------------------------------------------------------------ per-frame geometry

local function project(p)
	local s, on = Camera:WorldToViewportPoint(p)
	return Vector2.new(s.X, s.Y), s.Z > 0, on
end

local function getBounds(model, isR15)
	local list = isR15 and BOUND_PARTS_R15 or BOUND_PARTS_R6
	local minX, minY, maxX, maxY = math.huge, math.huge, -math.huge, -math.huge
	local any = false
	for _, name in ipairs(list) do
		local part = model:FindFirstChild(name)
		if part and part:IsA("BasePart") and not HITBOX_NAMES[part.Name] then
			local cf, half = part.CFrame, part.Size * 0.5
			for _, c in ipairs(CORNERS) do
				local s = Camera:WorldToViewportPoint(cf * (half * c))
				if s.Z > 0 then
					any = true
					if s.X < minX then minX = s.X end
					if s.Y < minY then minY = s.Y end
					if s.X > maxX then maxX = s.X end
					if s.Y > maxY then maxY = s.Y end
				end
			end
		end
	end
	if not any then return nil end
	local w, h = maxX - minX, maxY - minY
	-- humanoid boxes read better slightly padded and never razor thin
	w = math.max(w, h * 0.35)
	local cx = (minX + maxX) * 0.5
	return { x = cx - w * 0.5 - 2, y = minY - 2, w = w + 4, h = h + 4 }
end

local rayParams = RaycastParams.new()
rayParams.FilterType = Enum.RaycastFilterType.Exclude
rayParams.IgnoreWater = true

local function isVisible(e, targetPos)
	local filter = { e.model, Camera }
	if LocalPlayer.Character then table.insert(filter, LocalPlayer.Character) end
	rayParams.FilterDescendantsInstances = filter
	local origin = Camera.CFrame.Position
	local hit = Workspace:Raycast(origin, targetPos - origin, rayParams)
	return hit == nil
end

local function sameTeam(e)
	if not CONFIG.TeamCheck or e.kind ~= "Player" then return false end
	local p = e.player
	return p.Team ~= nil and p.Team == LocalPlayer.Team and #game:GetService("Teams"):GetTeams() > 1
end

------------------------------------------------------------------------ render

local function setLine(l, a, b, col, alpha)
	l.From, l.To, l.Color, l.Transparency, l.Visible = a, b, col, alpha, true
end

local function drawCorners(v, b, col, alpha)
	local x, y, w, h = b.x, b.y, b.w, b.h
	local lw, lh = math.max(w * 0.28, 4), math.max(h * 0.2, 4)
	local tl, tr = Vector2.new(x, y), Vector2.new(x + w, y)
	local bl, br = Vector2.new(x, y + h), Vector2.new(x + w, y + h)
	local segs = {
		{ tl, tl + Vector2.new(lw, 0) }, { tl, tl + Vector2.new(0, lh) },
		{ tr, tr - Vector2.new(lw, 0) }, { tr, tr + Vector2.new(0, lh) },
		{ bl, bl + Vector2.new(lw, 0) }, { bl, bl - Vector2.new(0, lh) },
		{ br, br - Vector2.new(lw, 0) }, { br, br - Vector2.new(0, lh) },
	}
	for i, s in ipairs(segs) do
		setLine(v.cornerOutlines[i], s[1], s[2], CONFIG.OutlineColor, alpha)
		setLine(v.corners[i], s[1], s[2], col, alpha)
	end
end

local function renderEntity(e, dt, now)
	local v, model, hum = e.v, e.model, e.hum
	local root = model:FindFirstChild("HumanoidRootPart") or model.PrimaryPart
	local head = model:FindFirstChild("Head")

	local alive = hum.Parent ~= nil and hum.Health > 0 and not e.dying
	local enabled = (e.kind == "Player") or (e.kind == "Bot" and CONFIG.ShowBots) or (e.kind == "Dummy" and CONFIG.ShowDummies)
	local dist = root and (root.Position - Camera.CFrame.Position).Magnitude or math.huge
	local inRange = dist <= CONFIG.MaxDistance
	local shouldShow = alive and enabled and inRange and root ~= nil and not sameTeam(e)

	e.alpha = approach(e.alpha, shouldShow and 1 or 0, shouldShow and CONFIG.FadeSpeed or CONFIG.FadeSpeed * 1.4, dt)
	if e.dying and e.alpha < 0.02 then return "remove" end
	if e.alpha < 0.02 or not root then
		hideAll(v)
		if not alive then e.box = nil end
		return
	end

	local isR15 = hum.RigType == Enum.HumanoidRigType.R15
	local target = getBounds(model, isR15)
	if not target then hideAll(v); return end

	-- smooth box; snap on teleport/respawn so it never sweeps across the screen
	if not e.box or math.abs(target.x - e.box.x) > 250 or math.abs(target.y - e.box.y) > 250 then
		e.box = target
	else
		local k = CONFIG.Smoothing
		e.box = {
			x = approach(e.box.x, target.x, k, dt), y = approach(e.box.y, target.y, k, dt),
			w = approach(e.box.w, target.w, k, dt), h = approach(e.box.h, target.h, k, dt),
		}
	end
	local b = e.box

	-- distance fade over the last 30% of range, plus a pop-in scale on spawn
	local fade = math.clamp(1 - (dist - CONFIG.MaxDistance * 0.7) / (CONFIG.MaxDistance * 0.3), 0.15, 1)
	local alpha = e.alpha * fade
	local pop = math.clamp((now - e.spawnT) / 0.35, 0, 1)
	pop = 1 - (1 - pop) ^ 3
	if pop < 1 then
		local s = 1.25 - 0.25 * pop
		b = { x = b.x + b.w * (1 - s) * 0.5, y = b.y + b.h * (1 - s) * 0.5, w = b.w * s, h = b.h * s }
	end

	-- occlusion, throttled
	if CONFIG.VisCheck then
		e.visT -= dt
		if e.visT <= 0 then
			e.visT = 0.08
			e.visible = isVisible(e, (head or root).Position)
		end
	else
		e.visible = true
	end

	local base = e.kind == "Player" and (e.player.Team and e.player.TeamColor.Color or CONFIG.PlayerColor)
		or (e.kind == "Bot" and CONFIG.BotColor or CONFIG.DummyColor)
	if e.kind == "Player" and #game:GetService("Teams"):GetTeams() <= 1 then base = CONFIG.PlayerColor end
	local col = e.visible and base or scale(base, CONFIG.HiddenTint)
	local breathe = 0.5 + 0.5 * math.sin(now * 3 + e.spawnT * 7)

	-------------------------------------------------- box
	local pos, size = Vector2.new(b.x, b.y), Vector2.new(b.w, b.h)
	if CONFIG.Box then
		if CONFIG.BoxFill then
			v.boxFill.Position, v.boxFill.Size = pos, size
			v.boxFill.Color = col
			v.boxFill.Transparency = alpha * (e.visible and 0.10 + 0.05 * breathe or 0.05)
			v.boxFill.Visible = true
		else
			v.boxFill.Visible = false
		end

		v.boxGlow.Position, v.boxGlow.Size = pos - Vector2.new(1, 1), size + Vector2.new(2, 2)
		v.boxGlow.Color = col
		v.boxGlow.Transparency = alpha * (e.visible and 0.12 + 0.18 * breathe or 0)
		v.boxGlow.Visible = e.visible

		if CONFIG.BoxStyle == "Full" then
			for i = 1, 8 do v.corners[i].Visible = false; v.cornerOutlines[i].Visible = false end
			v.boxOutline.Position, v.boxOutline.Size, v.boxOutline.Transparency, v.boxOutline.Visible = pos, size, alpha, true
			v.box.Position, v.box.Size, v.box.Color, v.box.Transparency, v.box.Visible = pos, size, col, alpha, true
		else
			v.boxOutline.Visible, v.box.Visible = false, false
			drawCorners(v, b, col, alpha)
		end
	else
		v.boxFill.Visible, v.boxGlow.Visible, v.boxOutline.Visible, v.box.Visible = false, false, false, false
		for i = 1, 8 do v.corners[i].Visible = false; v.cornerOutlines[i].Visible = false end
	end

	-------------------------------------------------- health bar
	local maxHp = math.max(hum.MaxHealth, 1)
	local hpTarget = math.clamp(hum.Health / maxHp, 0, 1)
	if hpTarget < e.hp - 0.001 then e.ghostHold = 0.35 end
	e.hp = approach(e.hp, hpTarget, 14, dt)
	if e.ghostHold > 0 then
		e.ghostHold -= dt
	else
		e.ghost = approach(e.ghost, e.hp, 4, dt)
	end
	if e.ghost < e.hp then e.ghost = e.hp end

	if CONFIG.HealthBar then
		local bx, by, bh = b.x - 6, b.y, b.h
		v.hpBack.Position, v.hpBack.Size = Vector2.new(bx - 1, by - 1), Vector2.new(4, bh + 2)
		v.hpBack.Transparency, v.hpBack.Visible = alpha * 0.85, true

		local gh = bh * e.ghost
		v.hpGhost.Position, v.hpGhost.Size = Vector2.new(bx, by + bh - gh), Vector2.new(2, gh)
		v.hpGhost.Transparency, v.hpGhost.Visible = alpha * 0.8, e.ghost - e.hp > 0.005

		local hh = bh * e.hp
		local hc = healthColor(e.hp)
		if e.hp < 0.3 then hc = hc:Lerp(Color3.new(1, 1, 1), 0.35 * (0.5 + 0.5 * math.sin(now * 12))) end
		v.hpBar.Position, v.hpBar.Size = Vector2.new(bx, by + bh - hh), Vector2.new(2, hh)
		v.hpBar.Color, v.hpBar.Transparency, v.hpBar.Visible = hc, alpha, true

		if e.hp < 0.99 then
			v.hpText.Text = tostring(math.floor(hum.Health + 0.5))
			v.hpText.Position = Vector2.new(bx - 10, by + bh - hh - 6)
			v.hpText.Color, v.hpText.Transparency, v.hpText.Visible = hc, alpha, true
		else
			v.hpText.Visible = false
		end
	else
		v.hpBack.Visible, v.hpGhost.Visible, v.hpBar.Visible, v.hpText.Visible = false, false, false, false
	end

	-------------------------------------------------- labels
	local cx = b.x + b.w * 0.5
	if CONFIG.Names then
		v.name.Text = e.name
		v.name.Position = Vector2.new(cx, b.y - 16)
		v.name.Color, v.name.Transparency, v.name.Visible = Color3.new(1, 1, 1):Lerp(col, 0.25), alpha, true

		if e.kind ~= "Player" then
			v.tag.Text = e.kind == "Bot" and "[ BOT ]" or "[ DUMMY ]"
			v.tag.Position = Vector2.new(cx, b.y - 28)
			v.tag.Color, v.tag.Transparency, v.tag.Visible = base, alpha * (0.7 + 0.3 * breathe), true
		else
			v.tag.Visible = false
		end
	else
		v.name.Visible, v.tag.Visible = false, false
	end

	if CONFIG.Distance then
		v.dist.Text = string.format("%dm", math.floor(dist / 3.571 + 0.5)) -- studs -> meters (0.28m/stud)
		v.dist.Position = Vector2.new(cx, b.y + b.h + 2)
		v.dist.Color, v.dist.Transparency, v.dist.Visible = Color3.fromRGB(200, 200, 210), alpha * 0.9, true
	else
		v.dist.Visible = false
	end

	-------------------------------------------------- skeleton
	local used = 0
	if CONFIG.Skeleton then
		local skCol = e.visible and Color3.new(1, 1, 1):Lerp(base, 0.45) or scale(base, CONFIG.HiddenTint)
		if isR15 then
			for _, bone in ipairs(R15_BONES) do
				local a, c = model:FindFirstChild(bone[1]), model:FindFirstChild(bone[2])
				if a and c then
					local pa, fa = project(a.Position)
					local pc, fc = project(c.Position)
					if fa and fc then
						used += 1
						setLine(v.boneOutlines[used], pa, pc, CONFIG.OutlineColor, alpha * 0.8)
						setLine(v.bones[used], pa, pc, skCol, alpha)
					end
				end
			end
		else
			for _, bone in ipairs(R6_BONES) do
				local a, c = model:FindFirstChild(bone[1]), model:FindFirstChild(bone[3])
				if a and c then
					local pa, fa = project((a.CFrame * CFrame.new(bone[2])).Position)
					local pc, fc = project((c.CFrame * CFrame.new(bone[4])).Position)
					if fa and fc then
						used += 1
						setLine(v.boneOutlines[used], pa, pc, CONFIG.OutlineColor, alpha * 0.8)
						setLine(v.bones[used], pa, pc, skCol, alpha)
					end
				end
			end
		end

		if CONFIG.HeadCircle and head then
			local top = project((head.CFrame * CFrame.new(0, head.Size.Y * 0.5, 0)).Position)
			local ctr, front = project(head.Position)
			if front then
				local r = math.max((top - ctr).Magnitude, 2.5)
				v.headOutline.Position, v.headOutline.Radius, v.headOutline.Transparency, v.headOutline.Visible = ctr, r, alpha * 0.8, true
				v.head.Position, v.head.Radius, v.head.Color, v.head.Transparency, v.head.Visible = ctr, r, skCol, alpha, true
			else
				v.headOutline.Visible, v.head.Visible = false, false
			end
		else
			v.headOutline.Visible, v.head.Visible = false, false
		end
	else
		v.headOutline.Visible, v.head.Visible = false, false
	end
	for i = used + 1, 14 do v.bones[i].Visible = false; v.boneOutlines[i].Visible = false end
end

------------------------------------------------------------------------ status HUD

local hud = text(13)
hud.Center = false
hud.Position = Vector2.new(14, 14)
local hudFlash = 0

local function hudText()
	local function f(b) return b and "on" or "off" end
	return string.format(
		"esp  ·  box %s [%s]  ·  skel %s  ·  vis %s  ·  bots %s",
		f(CONFIG.Box), CONFIG.BoxStyle:lower(), f(CONFIG.Skeleton), f(CONFIG.VisCheck), f(CONFIG.ShowBots))
end

------------------------------------------------------------------------ wiring

local function onHumanoid(hum)
	local model = hum.Parent
	if model and model:IsA("Model") then track(model) end
end

for _, d in ipairs(Workspace:GetDescendants()) do
	if d:IsA("Humanoid") then onHumanoid(d) end
end

table.insert(Connections, Workspace.DescendantAdded:Connect(function(d)
	if d:IsA("Humanoid") then
		task.defer(onHumanoid, d)  -- let the rig finish parenting its limbs
	end
end))

table.insert(Connections, Workspace:GetPropertyChangedSignal("CurrentCamera"):Connect(function()
	Camera = Workspace.CurrentCamera
end))

table.insert(Connections, UserInputService.InputBegan:Connect(function(input, gp)
	if gp then return end
	local k = input.KeyCode
	if k == Enum.KeyCode.F1 then CONFIG.Box = not CONFIG.Box
	elseif k == Enum.KeyCode.F2 then CONFIG.Skeleton = not CONFIG.Skeleton
	elseif k == Enum.KeyCode.F3 then CONFIG.VisCheck = not CONFIG.VisCheck
	elseif k == Enum.KeyCode.F4 then CONFIG.BoxStyle = CONFIG.BoxStyle == "Corner" and "Full" or "Corner"
	elseif k == Enum.KeyCode.F5 then CONFIG.ShowBots = not CONFIG.ShowBots
	elseif k == Enum.KeyCode.End then getgenv().__MARA_ESP(); return
	else return end
	hudFlash = 1
end))

table.insert(Connections, RunService.RenderStepped:Connect(function(dt)
	local now = os.clock()
	for model, e in pairs(Entities) do
		-- a respawned humanoid replaces the old one inside the same model on some rigs
		if e.hum.Parent ~= model then
			local h = model:FindFirstChildOfClass("Humanoid")
			if h then e.hum = h; e.box = nil; e.spawnT = now; e.hp, e.ghost = 1, 1 end
		end
		local ok, res = pcall(renderEntity, e, dt, now)
		if (ok and res == "remove") or not model.Parent and e.alpha < 0.02 then
			destroyEntity(model)
		end
	end

	hudFlash = approach(hudFlash, 0, 3, dt)
	hud.Text = hudText()
	hud.Color = Color3.fromRGB(150, 150, 165):Lerp(Color3.fromRGB(255, 255, 255), hudFlash)
	hud.Transparency = 0.55 + 0.45 * hudFlash
	hud.Visible = true
end))

getgenv().__MARA_ESP = function()
	for _, c in ipairs(Connections) do c:Disconnect() end
	table.clear(Connections)
	for model in pairs(Entities) do destroyEntity(model) end
	pcall(hud.Remove, hud)
	getgenv().__MARA_ESP = nil
end
