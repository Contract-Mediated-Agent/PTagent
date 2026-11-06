"""Shared CMake setup for task-local PhaseTracer build products."""


def dependency_cmake() -> str:
    return r'''# Do not register this task's build in the user's CMake package registry.
set(CMAKE_EXPORT_NO_PACKAGE_REGISTRY ON)
set(CMAKE_EXPORT_PACKAGE_REGISTRY OFF)
set(CMAKE_FIND_USE_PACKAGE_REGISTRY OFF)
set(CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY OFF)
set(PHASETRACER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(PHASETRACER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_WITH_EXAMPLE OFF CACHE BOOL "" FORCE)
add_subdirectory("${PHASETRACER_ROOT}" "${CMAKE_BINARY_DIR}/PhaseTracer" EXCLUDE_FROM_ALL)

# Upstream targets may set source-relative lib/bin directories. Override the
# target properties after creation, including multi-configuration generators.
function(ptagent_localize_outputs directory)
  get_property(targets DIRECTORY "${directory}" PROPERTY BUILDSYSTEM_TARGETS)
  foreach(target IN LISTS targets)
    get_target_property(imported "${target}" IMPORTED)
    get_target_property(kind "${target}" TYPE)
    if(NOT imported AND NOT kind STREQUAL "INTERFACE_LIBRARY" AND NOT kind STREQUAL "UTILITY")
      foreach(output LIBRARY ARCHIVE RUNTIME PDB COMPILE_PDB)
        set_target_properties("${target}" PROPERTIES
          "${output}_OUTPUT_DIRECTORY" "${CMAKE_BINARY_DIR}/ptagent-deps/$<CONFIG>")
        foreach(config IN LISTS CMAKE_CONFIGURATION_TYPES)
          string(TOUPPER "${config}" config_upper)
          set_target_properties("${target}" PROPERTIES
            "${output}_OUTPUT_DIRECTORY_${config_upper}" "${CMAKE_BINARY_DIR}/ptagent-deps/${config}")
        endforeach()
      endforeach()
    endif()
  endforeach()
  get_property(children DIRECTORY "${directory}" PROPERTY SUBDIRECTORIES)
  foreach(child IN LISTS children)
    ptagent_localize_outputs("${child}")
  endforeach()
endfunction()
ptagent_localize_outputs("${PHASETRACER_ROOT}")
'''
