#pragma once
#include <string>

namespace rl {

// Click-through topmost toast in the top-left corner. Visible over borderless-windowed games.
class Overlay {
public:
    bool Create(void* hInstance);
    void Destroy();
    void Show(const std::string& line1, const std::string& line2, unsigned accentRgb);
    void Tick();  // fades out; call every frame
private:
    void Paint();
    void* hwnd_ = nullptr;
    std::string l1_, l2_;
    unsigned accent_ = 0;
    double shownAt_ = 0;
    int alpha_ = 0;
};

}  // namespace rl
