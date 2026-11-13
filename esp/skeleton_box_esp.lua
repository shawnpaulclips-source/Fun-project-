--[[
	skeleton + box esp  —  place 118245546740416
	players + bots (Dummy-template rigs tagged with BotUsername) + training dummies

	F1  box          F2  skeleton       F3  visibility check
	F4  box style    F5  bots on/off    F6  silent aim    F7  wall mode (experimental)
	F8  wall origin: camera / past-wall
	[ / ]  shrink / grow fov            END unload
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
	DeathStyle    = "Wither", -- "Wither" | "Instant"
	WitherTime    = 0.8,
	AshColor      = Color3.fromRGB(90, 86, 82),
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

	SilentAim     = true,
	AimWallCheck  = true,     -- server rejects shots a wall eats first, so skip occluded heads
	AimAtDummies  = false,
	AimPriority   = "Distance", -- "Distance" (closest in 3D inside the wheel) | "Crosshair"
	WallMode      = false,      -- F7: target + fire through walls
	WallOrigin    = "Camera",   -- F8: "Camera" (real origin in packet) | "PastWall" (origin just past the last wall)
	WallColor     = Color3.fromRGB(190, 110, 255),
	FOVRadius     = 140,
	FOVMin        = 40,
	FOVMax        = 500,
	FOVColor      = Color3.fromRGB(235, 235, 245),
	FOVLockColor  = Color3.fromRGB(255, 70, 95),
	AimLineColor  = Color3.fromRGB(255, 70, 95),
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
	-- executors disagree on which Drawing props exist; one unknown prop must not kill the script
	for k, v in pairs(props) do pcall(function() d[k] = v end) end
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

local IGNORE_FOLDERS = { "Corpses", "Effects", "GlassDebris", "ExplosiveDebris" }

local function inIgnoredFolder(inst)
	for _, n in ipairs(IGNORE_FOLDERS) do
		local f = Workspace:FindFirstChild(n)
		if f and inst:IsDescendantOf(f) then return true end
	end
	return false
end

local function track(model)
	if Entities[model] or model == LocalPlayer.Character then return end
	if inIgnoredFolder(model) then return end
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
		if not parent then
			e.dying = true
		elseif e.killed then
			e.killed, e.sawHidden = nil, false  -- moved to lobby folder = reused
		end
	end))
	table.insert(e.conns, model:GetAttributeChangedSignal("InRound"):Connect(function()
		if model:GetAttribute("InRound") == true then e.killed, e.sawHidden = nil, false end
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

local function getBones(model, isR15)
	local out = {}
	if isR15 then
		for _, bone in ipairs(R15_BONES) do
			local a, c = model:FindFirstChild(bone[1]), model:FindFirstChild(bone[2])
			if a and c then table.insert(out, { a.Position, c.Position }) end
		end
	else
		for _, bone in ipairs(R6_BONES) do
			local a, c = model:FindFirstChild(bone[1]), model:FindFirstChild(bone[3])
			if a and c then
				table.insert(out, { (a.CFrame * CFrame.new(bone[2])).Position, (c.CFrame * CFrame.new(bone[4])).Position })
			end
		end
	end
	return out
end

-- CorpseController never kills the character: it clones a ragdoll into Corpses and forces
-- LocalTransparencyModifier = 1 on the real model every frame while the server reuses it.
-- So "dead" = kill event seen, or body locally hidden, or the classic humanoid signals.
local function bodyHidden(model)
	local h = model:FindFirstChild("Head") or model:FindFirstChild("UpperTorso") or model:FindFirstChild("Torso")
	return h ~= nil and h:IsA("BasePart") and h.LocalTransparencyModifier >= 0.99
end

local function isAlive(e)
	local hum = e.hum
	if hum.Parent == nil or hum.Health <= 0 or hum:GetState() == Enum.HumanoidStateType.Dead then return false end
	local hidden = bodyHidden(e.model)
	if hidden then e.sawHidden = true end
	if e.killed then
		-- revive: unhidden after being hidden, or 8s safety (e.g. corpses lever off and never hidden)
		if (e.sawHidden and not hidden) or os.clock() - e.killed > 8 then
			e.killed, e.sawHidden = nil, false
		else
			return false
		end
	end
	return not hidden
end

local function markKilled(victim)
	local model = victim
	if typeof(victim) == "Instance" and victim:IsA("Player") then model = victim.Character end
	local e = model and Entities[model]
	if e then e.killed, e.sawHidden = os.clock(), false end
end

-- frozen world-space skeleton crumbles: limbs first, each bone shrinks to its midpoint,
-- sags, greys to ash and fades; head drops and collapses last
local function renderDeath(e, now)
	local v, el = e.v, now - e.deathT
	local T = CONFIG.WitherTime
	hideAll(v)
	if CONFIG.DeathStyle ~= "Wither" or not CONFIG.Skeleton or el > T + 0.15 or e.deathAlpha < 0.02 then
		return
	end

	local n = #e.deathBones
	local used = 0
	for i, bone in ipairs(e.deathBones) do
		local order = n - i                       -- extremities are listed last, so they go first
		local k = math.clamp((el - order * (T * 0.03)) / (T * 0.6), 0, 1)
		local ease = k * k * (3 - 2 * k)
		local a, b = bone[1], bone[2]
		local mid = (a + b) * 0.5
		local sag = Vector3.new(math.sin(i * 1.7) * 0.4 * ease, -1.6 * ease * ease, math.cos(i * 2.3) * 0.4 * ease)
		local pa, fa = project(mid + (a - mid) * (1 - ease) + sag)
		local pb, fb = project(mid + (b - mid) * (1 - ease) + sag)
		if fa and fb and ease < 0.99 then
			used += 1
			local col = e.deathColor:Lerp(CONFIG.AshColor, math.min(k * 1.6, 1))
			local al = e.deathAlpha * (1 - ease)
			setLine(v.boneOutlines[used], pa, pb, CONFIG.OutlineColor, al * 0.7)
			setLine(v.bones[used], pa, pb, col, al)
			v.bones[used].Thickness = 1.5 * (1 - 0.6 * ease)
		end
	end

	if e.deathHead then
		local k = math.clamp((el - n * T * 0.03) / (T * 0.5), 0, 1)
		local ease = k * k
		local ctr, front = project(e.deathHead - Vector3.new(0, 1.2 * ease, 0))
		if front and ease < 0.99 then
			local r = e.deathHeadR * (1 - 0.8 * ease)
			local col = e.deathColor:Lerp(CONFIG.AshColor, k)
			v.headOutline.Position, v.headOutline.Radius, v.headOutline.Transparency, v.headOutline.Visible = ctr, r, e.deathAlpha * (1 - ease) * 0.7, true
			v.head.Position, v.head.Radius, v.head.Color, v.head.Transparency, v.head.Visible = ctr, r, col, e.deathAlpha * (1 - ease), true
		end
	end
end

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

	local alive = isAlive(e) and not e.dying

	if alive then
		if e.deathT then
			-- same model came back (bots respawn in place): fresh pop-in, no stale box
			e.deathT, e.box, e.alpha, e.spawnT, e.hp, e.ghost = nil, nil, 0, now, 1, 1
		end
		e.wasAlive = true
	elseif e.wasAlive and not e.deathT then
		e.deathT = now
		e.deathAlpha = e.alpha
		e.deathColor = e.lastSkCol or Color3.new(1, 1, 1)
		e.deathBones = getBones(model, hum.RigType == Enum.HumanoidRigType.R15)
		e.deathHead = head and head.Position
		e.deathHeadR = e.lastHeadR or 4
		e.alpha = 0
	end

	if e.deathT then
		renderDeath(e, now)
		if e.dying and now - e.deathT > CONFIG.WitherTime + 0.2 then return "remove" end
		return
	end
	local enabled = (e.kind == "Player") or (e.kind == "Bot" and CONFIG.ShowBots) or (e.kind == "Dummy" and CONFIG.ShowDummies)
	local dist = root and (root.Position - Camera.CFrame.Position).Magnitude or math.huge
	local inRange = dist <= CONFIG.MaxDistance
	local shouldShow = alive and enabled and inRange and root ~= nil and not sameTeam(e)

	e.alpha = approach(e.alpha, shouldShow and 1 or 0, shouldShow and CONFIG.FadeSpeed or CONFIG.FadeSpeed * 1.4, dt)
	if e.dying and e.alpha < 0.02 and not e.wasAlive then return "remove" end
	if e.dying and e.alpha < 0.02 and not e.deathT then return "remove" end
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
		e.lastSkCol = skCol
		for _, bone in ipairs(getBones(model, isR15)) do
			local pa, fa = project(bone[1])
			local pc, fc = project(bone[2])
			if fa and fc then
				used += 1
				setLine(v.boneOutlines[used], pa, pc, CONFIG.OutlineColor, alpha * 0.8)
				setLine(v.bones[used], pa, pc, skCol, alpha)
				v.bones[used].Thickness = 1.5
			end
		end

		if CONFIG.HeadCircle and head then
			local top = project((head.CFrame * CFrame.new(0, head.Size.Y * 0.5, 0)).Position)
			local ctr, front = project(head.Position)
			if front then
				local r = math.max((top - ctr).Magnitude, 2.5)
				e.lastHeadR = r
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

------------------------------------------------------------------------ silent aim
--[[
	WeaponController.request_fire reads workspace.CurrentCamera.CFrame exactly once and
	uses that Position/LookVector for the client hit cast, the tracer, AND the zap.shoot
	packet the server validates. Spoofing that single read for the duration of the call
	makes every stage agree on the head — no desync between what you see and what lands.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local function tagFrom(name)
	local ok, v = pcall(function()
		return require(ReplicatedStorage.shared.combat[name])
	end)
	return ok and type(v) == "string" and v or nil
end
local BARRIER_TAG, GLASS_TAG = tagFrom("barrier_tag"), tagFrom("glass_tag")

-- every live table that owns request_fire; an executor require can hand back a copy,
-- so wrap the require result and anything getgc finds
local function findWeaponControllers()
	local found, seen = {}, {}
	local function add(t)
		if type(t) == "table" and not seen[t] and rawget(t, "request_fire") and rawget(t, "compute_player_radius") then
			seen[t] = true
			table.insert(found, t)
		end
	end
	pcall(function()
		add(require(ReplicatedStorage.client.controllers.WeaponController))
	end)
	if getgc then
		pcall(function()
			for _, t in ipairs(getgc(true)) do add(t) end
		end)
	end
	return found
end

local Aim = {
	target = nil,        -- { e = entity, part = BasePart }
	lockT = 0,           -- 0..1 line extension when a new target locks
	lineEnd = nil,
	lockLevel = 0,       -- smoothed 0..1, drives ring colour/spin
	spin = 0,
	shotT = 0,           -- 1 on fire, decays
	shotPos = nil,
	ripples = {},
}

local spoofCF, spoofCam = nil, nil

local function aimPart(model)
	local p = model:FindFirstChild("HeadHitbox") or model:FindFirstChild("Head")
	return (p and p:IsA("BasePart")) and p or nil
end

local aimRay = RaycastParams.new()
aimRay.FilterType = Enum.RaycastFilterType.Exclude
aimRay.IgnoreWater = true

-- build the exclude list matching the game's own world-cast exclusions
local function buildAimExclude(model)
	local ex = { model, Camera }
	if LocalPlayer.Character then table.insert(ex, LocalPlayer.Character) end
	for _, n in ipairs({ "Effects", "Corpses", "GlassDebris", "ExplosiveDebris" }) do
		local f = Workspace:FindFirstChild(n)
		if f then table.insert(ex, f) end
	end
	for _, p in ipairs(Players:GetPlayers()) do
		if p.Character then table.insert(ex, p.Character) end
	end
	-- exclude bot and dummy models so their bodies don't count as occluders
	for m, e in pairs(Entities) do
		if m ~= model and (e.kind == "Bot" or e.kind == "Dummy") and m.Parent then
			table.insert(ex, m)
		end
	end
	if BARRIER_TAG then for _, x in ipairs(CollectionService:GetTagged(BARRIER_TAG)) do table.insert(ex, x) end end
	if GLASS_TAG then for _, x in ipairs(CollectionService:GetTagged(GLASS_TAG)) do table.insert(ex, x) end end
	return ex
end

-- mirror the game's own world-cast exclusions so "visible" means "the shot connects"
local function aimVisible(model, point)
	aimRay.FilterDescendantsInstances = buildAimExclude(model)
	local o = Camera.CFrame.Position
	return Workspace:Raycast(o, point - o, aimRay) == nil
end

-- cast from the head back toward the camera: the first surface hit is the wall nearest the
-- target, so an origin just on the head side of it has clean line of sight with minimal shift
local function pastWallOrigin(model, head, camPos)
	aimRay.FilterDescendantsInstances = buildAimExclude(model)
	local hit = Workspace:Raycast(head, camPos - head, aimRay)
	if not hit then return camPos end
	local toHead = head - hit.Position
	local dist = toHead.Magnitude
	if dist < 0.01 then return head end
	local offset = math.min(0.6, dist * 0.8)
	return hit.Position + toHead.Unit * offset
end

local function pickTarget(center)
	-- gather everything inside the wheel, cheapest score first, then wall-check in that order
	local camPos = Camera.CFrame.Position
	local cands = {}
	for model, e in pairs(Entities) do
		if not e.dying and not e.deathT and model.Parent and isAlive(e)
			and (e.kind ~= "Dummy" or CONFIG.AimAtDummies) and not sameTeam(e) then
			local part = aimPart(model)
			if part then
				local s = Camera:WorldToViewportPoint(part.Position)
				if s.Z > 0 then
					local sd = (Vector2.new(s.X, s.Y) - center).Magnitude
					if sd <= CONFIG.FOVRadius then
						local score = CONFIG.AimPriority == "Crosshair" and sd or (part.Position - camPos).Magnitude
						table.insert(cands, { e = e, part = part, score = score })
					end
				end
			end
		end
	end
	table.sort(cands, function(a, b) return a.score < b.score end)
	local preview
	for _, c in ipairs(cands) do
		local vis = aimVisible(c.e.model, c.part.Position)
		if vis or CONFIG.WallMode or not CONFIG.AimWallCheck then
			c.occluded = not vis
			return c
		end
		if not preview then
			c.occluded, c.preview = true, true
			preview = c
		end
	end
	-- wall mode off: still draw a line to the nearest head behind a wall, but never fire at it
	return preview
end

local function onShot()
	Aim.shotT = 1
	if Aim.target and not Aim.target.preview then
		Aim.shotPos = Aim.target.part.Position
		table.insert(Aim.ripples, { pos = Aim.shotPos, t = 0 })
	end
end

local Hook = { controllers = {}, originals = {}, signals = {}, mode = "none", wallMode = "origin", wallShot = false, status = "not installed" }
local oldIndex, oldNamecall

local function aimCFrameFor(cam)
	local t = Aim.target
	if not (CONFIG.SilentAim and t and not t.preview and t.part.Parent and isAlive(t.e)) then return nil end
	local head = t.part.Position
	local origin = cam.CFrame.Position
	if CONFIG.WallMode and t.occluded and (Hook.wallMode == "origin" or CONFIG.WallOrigin == "PastWall") then
		origin = pastWallOrigin(t.e.model, head, origin)
	end
	return CFrame.lookAt(origin, head)
end

local function makeWrapper(orig)
	return function(self, ...)
		local cam = Workspace.CurrentCamera
		local aimCF = cam and aimCFrameFor(cam)
		local restoreCF

		local t = Aim.target
		Hook.wallShot = aimCF ~= nil and CONFIG.WallMode and t ~= nil and t.occluded and Hook.wallMode == "namecall"
		if aimCF then
			if Hook.mode == "index" then
				spoofCam, spoofCF = cam, aimCF
			else
				-- no __index hook: point the real camera for this synchronous call, then put it back
				restoreCF = cam.CFrame
				cam.CFrame = aimCF
			end
		end

		local before = self.last_fire_clock
		local ok, err = pcall(orig, self, ...)

		spoofCF, spoofCam = nil, nil
		Hook.wallShot = false
		if restoreCF then cam.CFrame = restoreCF end
		if self.last_fire_clock ~= before then onShot() end
		if not ok then warn("[esp] request_fire:", err) end
	end
end

local function installHooks()
	local list = findWeaponControllers()
	if #list == 0 then
		Hook.status = "WeaponController not found"
		return
	end

	if hookmetamethod and checkcaller then
		local ok = pcall(function()
			local fn = function(self, key)
				if spoofCF and key == "CFrame" and self == spoofCam and not checkcaller() then
					return spoofCF
				end
				return oldIndex(self, key)
			end
			oldIndex = hookmetamethod(game, "__index", newcclosure and newcclosure(fn) or fn)
		end)
		Hook.mode = ok and oldIndex and "index" or "camera"
	else
		Hook.mode = "camera"
	end

	-- wall mode: perform_dual_cast's world Raycast (Exclude filter) is the only thing that can
	-- stop a hit; the hitbox Spherecasts use Include filters with characters only. Null the world
	-- ray for the duration of a wall shot and the head spherecast lands.
	Hook.wallMode = "origin"
	if hookmetamethod and getnamecallmethod and checkcaller then
		local ok = pcall(function()
			local fn = function(self, ...)
				if Hook.wallShot and self == Workspace and not checkcaller() and getnamecallmethod() == "Raycast" then
					local params = select(3, ...)
					if typeof(params) == "RaycastParams" and params.FilterType == Enum.RaycastFilterType.Exclude then
						return nil
					end
				end
				return oldNamecall(self, ...)
			end
			oldNamecall = hookmetamethod(game, "__namecall", newcclosure and newcclosure(fn) or fn)
		end)
		if ok and oldNamecall then Hook.wallMode = "namecall" end
	end

	for _, t in ipairs(list) do
		for _, sig in ipairs({ rawget(t, "kill_effect"), rawget(t, "dummy_kill_effect") }) do
			if type(sig) == "table" then
				local fn = function(victim) markKilled(victim) end
				local ok, c = pcall(function() return sig:connect(fn) end)
				if not ok then ok, c = pcall(function() return sig:Connect(fn) end) end
				if ok and c then table.insert(Hook.signals, c) end
			end
		end
	end

	local wrapped = 0
	for _, t in ipairs(list) do
		local orig = rawget(t, "request_fire")
		local ok = pcall(function()
			if setreadonly then pcall(setreadonly, t, false) end
			t.request_fire = makeWrapper(orig)
		end)
		if ok then
			wrapped += 1
			table.insert(Hook.controllers, t)
			table.insert(Hook.originals, orig)
		end
	end
	Hook.status = wrapped > 0 and ("hooked x" .. wrapped .. " · " .. Hook.mode) or "controller locked"
end

local function removeHooks()
	for i, t in ipairs(Hook.controllers) do
		pcall(function() t.request_fire = Hook.originals[i] end)
	end
	if oldIndex then pcall(hookmetamethod, game, "__index", oldIndex) end
	if oldNamecall then pcall(hookmetamethod, game, "__namecall", oldNamecall) end
	Hook.wallShot = false
	for _, c in ipairs(Hook.signals) do
		pcall(function() if c.Disconnect then c:Disconnect() elseif c.disconnect then c:disconnect() end end)
	end
	spoofCF, spoofCam = nil, nil
end

-- FOV wheel drawings
local TICKS = 32
local fov = {
	glow      = new("Circle", { Thickness = 7, NumSides = 96, Filled = false, Visible = false, ZIndex = 5 }),
	ringOut   = new("Circle", { Thickness = 3.5, NumSides = 96, Filled = false, Visible = false, ZIndex = 6, Color = CONFIG.OutlineColor }),
	ring      = new("Circle", { Thickness = 1.5, NumSides = 96, Filled = false, Visible = false, ZIndex = 7 }),
	inner     = new("Circle", { Thickness = 1, NumSides = 64, Filled = false, Visible = false, ZIndex = 6 }),
	dotOut    = new("Circle", { Thickness = 1, NumSides = 16, Filled = true, Visible = false, ZIndex = 9, Color = CONFIG.OutlineColor }),
	dot       = new("Circle", { Thickness = 1, NumSides = 16, Filled = true, Visible = false, ZIndex = 10 }),
	lineOut   = line(4.5, 7),
	line      = line(2, 8),
	lineFlash = line(5, 8),
	retOut    = new("Circle", { Thickness = 3.5, NumSides = 32, Filled = false, Visible = false, ZIndex = 8, Color = CONFIG.OutlineColor }),
	ret       = new("Circle", { Thickness = 1.5, NumSides = 32, Filled = false, Visible = false, ZIndex = 9 }),
	retDot    = new("Circle", { Thickness = 1, NumSides = 12, Filled = true, Visible = false, ZIndex = 9 }),
	ticks     = {},
	ripples   = {},
}
fov.lineOut.Color = CONFIG.OutlineColor
for i = 1, TICKS do fov.ticks[i] = line(2, 7) end
for i = 1, 4 do
	fov.ripples[i] = new("Circle", { Thickness = 2, NumSides = 40, Filled = false, Visible = false, ZIndex = 8 })
end

local function hideFov()
	for k, d in pairs(fov) do
		if k == "ticks" or k == "ripples" then
			for _, x in ipairs(d) do x.Visible = false end
		else
			d.Visible = false
		end
	end
end

local function easeOutBack(x)
	local c1, c3 = 1.70158, 2.70158
	return 1 + c3 * (x - 1) ^ 3 + c1 * (x - 1) ^ 2
end

local function updateAim(dt, now)
	if not CONFIG.SilentAim then hideFov(); Aim.target = nil; return end

	local vp = Camera.ViewportSize
	local center = Vector2.new(vp.X * 0.5, vp.Y * 0.5)

	local prev = Aim.target
	Aim.target = pickTarget(center)
	local t = Aim.target
	if t and (not prev or prev.e ~= t.e) then
		Aim.lockT = 0
		if not prev then
			Aim.growing, Aim.lineEnd = true, center
		else
			Aim.growing, Aim.lockT = false, 0.55  -- switch: slide the tip, quick reticle snap
		end
	end

	Aim.lockLevel = approach(Aim.lockLevel, t and 1 or 0, 10, dt)
	Aim.shotT = approach(Aim.shotT, 0, 7, dt)
	Aim.spin = (Aim.spin + dt * (0.35 + 2.4 * Aim.lockLevel + 6 * Aim.shotT)) % (math.pi * 2)

	local lock = Aim.lockLevel
	local lockCol = (t and t.occluded) and CONFIG.WallColor or CONFIG.FOVLockColor
	local ringCol = CONFIG.FOVColor:Lerp(lockCol, lock)
	local pulse = 0.5 + 0.5 * math.sin(now * (2.2 + 3 * lock))
	local r = CONFIG.FOVRadius * (1 + 0.025 * pulse + 0.06 * Aim.shotT)

	fov.glow.Position, fov.glow.Radius, fov.glow.Color = center, r, ringCol
	fov.glow.Transparency, fov.glow.Visible = 0.06 + 0.14 * pulse + 0.3 * Aim.shotT, true
	fov.ringOut.Position, fov.ringOut.Radius, fov.ringOut.Transparency, fov.ringOut.Visible = center, r, 0.55, true
	fov.ring.Position, fov.ring.Radius, fov.ring.Color, fov.ring.Transparency, fov.ring.Visible = center, r, ringCol, 0.75 + 0.25 * pulse, true
	fov.inner.Position, fov.inner.Radius, fov.inner.Color = center, r * (0.93 - 0.03 * pulse), ringCol
	fov.inner.Transparency, fov.inner.Visible = 0.12 + 0.12 * lock, true

	-- the wheel: ticks spin, every 4th one is long, lock tightens them inward
	for i = 1, TICKS do
		local a = Aim.spin + (i - 1) / TICKS * math.pi * 2
		local major = (i - 1) % 4 == 0
		local len = (major and 9 or 4) * (1 + 0.6 * lock)
		local dir = Vector2.new(math.cos(a), math.sin(a))
		local tk = fov.ticks[i]
		tk.From, tk.To = center + dir * (r + 3), center + dir * (r + 3 + len)
		tk.Color = ringCol
		tk.Thickness = major and 2 or 1.25
		tk.Transparency = (major and 0.9 or 0.45) * (0.6 + 0.4 * pulse)
		tk.Visible = true
	end

	local dotR = 2.6 + 0.8 * lock + 2 * Aim.shotT
	fov.dotOut.Position, fov.dotOut.Radius, fov.dotOut.Transparency, fov.dotOut.Visible = center, dotR + 1.5, 0.9, true
	fov.dot.Position, fov.dot.Radius, fov.dot.Color, fov.dot.Transparency, fov.dot.Visible =
		center, dotR, Color3.new(1, 1, 1):Lerp(CONFIG.AimLineColor, lock), 1, true

	if t then
		local hs = Camera:WorldToViewportPoint(t.part.Position)
		local head = Vector2.new(hs.X, hs.Y)
		local tip
		if Aim.growing then
			Aim.lockT = math.min(Aim.lockT + dt / 0.08, 1)
			tip = center:Lerp(head, easeOutBack(Aim.lockT))
			if Aim.lockT >= 1 then Aim.growing, tip = false, head end
			Aim.lineEnd = tip
		else
			Aim.lockT = math.min(Aim.lockT + dt / 0.06, 1)
			Aim.lineEnd = (Aim.lineEnd or head):Lerp(head, 1 - math.exp(-75 * dt))
			if (Aim.lineEnd - head).Magnitude < 1.5 then Aim.lineEnd = head end
			tip = Aim.lineEnd
		end
		-- the shot always goes to the true head; the tip is glued there except mid-slide
		if Aim.shotT > 0.6 then tip, Aim.lineEnd = head, head end

		local baseLc = t.occluded and CONFIG.WallColor or CONFIG.AimLineColor
		local lc = baseLc:Lerp(Color3.new(1, 1, 1), Aim.shotT * 0.8)
		-- preview (wall mode off) flickers dim so it reads as "not armed"
		local lineA = t.preview and (0.25 + 0.3 * pulse) or (0.85 + 0.15 * pulse)
		setLine(fov.lineOut, center, tip, CONFIG.OutlineColor, t.preview and 0.35 or 0.8)
		setLine(fov.line, center, tip, lc, lineA)
		fov.line.Thickness = 2 + 2 * Aim.shotT
		if Aim.shotT > 0.02 then
			setLine(fov.lineFlash, center, tip, Color3.new(1, 1, 1), Aim.shotT * 0.55)
		else
			fov.lineFlash.Visible = false
		end

		local hr = Camera:WorldToViewportPoint((t.part.CFrame * CFrame.new(0, t.part.Size.Y * 0.5, 0)).Position)
		local headR = math.max((Vector2.new(hr.X, hr.Y) - head).Magnitude, 3)
		local retR = headR + 4 + 10 * (1 - Aim.lockT) + 3 * pulse
		fov.retOut.Position, fov.retOut.Radius, fov.retOut.Transparency, fov.retOut.Visible = tip, retR, 0.7, true
		fov.ret.Position, fov.ret.Radius, fov.ret.Color, fov.ret.Transparency, fov.ret.Visible = tip, retR, lc, Aim.lockT, true
		fov.retDot.Position, fov.retDot.Radius, fov.retDot.Color, fov.retDot.Transparency, fov.retDot.Visible = tip, 2, lc, Aim.lockT, true
	else
		Aim.lineEnd = nil
		fov.lineOut.Visible, fov.line.Visible, fov.lineFlash.Visible = false, false, false
		fov.retOut.Visible, fov.ret.Visible, fov.retDot.Visible = false, false, false
	end

	-- hit ripples anchored in world space so they stay on the spot you shot
	for i = #Aim.ripples, 1, -1 do
		local rp = Aim.ripples[i]
		rp.t += dt / 0.45
		if rp.t >= 1 then table.remove(Aim.ripples, i) end
	end
	while #Aim.ripples > #fov.ripples do table.remove(Aim.ripples, 1) end
	for i, c in ipairs(fov.ripples) do
		local rp = Aim.ripples[i]
		if rp then
			local s = Camera:WorldToViewportPoint(rp.pos)
			if s.Z > 0 then
				local k = 1 - (1 - rp.t) ^ 3
				c.Position, c.Radius = Vector2.new(s.X, s.Y), 4 + 26 * k
				c.Color, c.Transparency, c.Thickness = CONFIG.AimLineColor:Lerp(Color3.new(1, 1, 1), 1 - rp.t), (1 - rp.t) * 0.9, 2.5 - 1.5 * k
				c.Visible = true
			else
				c.Visible = false
			end
		else
			c.Visible = false
		end
	end
end

------------------------------------------------------------------------ status HUD

local hud = text(13)
hud.Center = false
hud.Position = Vector2.new(14, 14)
local hudFlash = 0

local function hudText()
	local function f(b) return b and "on" or "off" end
	return string.format(
		"esp  ·  box %s [%s]  ·  skel %s  ·  vis %s  ·  bots %s  ·  silent %s [%d]%s",
		f(CONFIG.Box), CONFIG.BoxStyle:lower(), f(CONFIG.Skeleton), f(CONFIG.VisCheck), f(CONFIG.ShowBots),
		f(CONFIG.SilentAim), CONFIG.FOVRadius,
		(CONFIG.WallMode and ("  ·  WALLS [" .. Hook.wallMode .. (Hook.wallMode == "namecall" and (" / " .. CONFIG.WallOrigin:lower()) or " / shifted") .. "]") or "") .. "  ·  " .. Hook.status)
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

table.insert(Connections, UserInputService.InputBegan:Connect(function(input)
	-- the game sinks keys through its own input stack, so gameProcessed is unreliable; only skip typing
	if UserInputService:GetFocusedTextBox() then return end
	local k = input.KeyCode
	if k == Enum.KeyCode.F1 then CONFIG.Box = not CONFIG.Box
	elseif k == Enum.KeyCode.F2 then CONFIG.Skeleton = not CONFIG.Skeleton
	elseif k == Enum.KeyCode.F3 then CONFIG.VisCheck = not CONFIG.VisCheck
	elseif k == Enum.KeyCode.F4 then CONFIG.BoxStyle = CONFIG.BoxStyle == "Corner" and "Full" or "Corner"
	elseif k == Enum.KeyCode.F5 then CONFIG.ShowBots = not CONFIG.ShowBots
	elseif k == Enum.KeyCode.F6 then CONFIG.SilentAim = not CONFIG.SilentAim
	elseif k == Enum.KeyCode.F7 then CONFIG.WallMode = not CONFIG.WallMode
	elseif k == Enum.KeyCode.F8 then CONFIG.WallOrigin = CONFIG.WallOrigin == "Camera" and "PastWall" or "Camera"
	elseif k == Enum.KeyCode.LeftBracket then CONFIG.FOVRadius = math.max(CONFIG.FOVMin, CONFIG.FOVRadius - 20)
	elseif k == Enum.KeyCode.RightBracket then CONFIG.FOVRadius = math.min(CONFIG.FOVMax, CONFIG.FOVRadius + 20)
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
		if (ok and res == "remove") or (not ok and not model.Parent) then
			destroyEntity(model)
		end
	end

	local ok, err = pcall(updateAim, dt, now)
	if not ok then warn("[esp] aim:", err) end

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
	removeHooks()
	for k, d in pairs(fov) do
		if k == "ticks" or k == "ripples" then
			for _, x in ipairs(d) do pcall(x.Remove, x) end
		else
			pcall(d.Remove, d)
		end
	end
	getgenv().__MARA_ESP = nil
end

task.spawn(function()
	local ok, err = pcall(installHooks)
	if not ok then Hook.status = "hook error"; warn("[esp] hook install:", err) end
end)
